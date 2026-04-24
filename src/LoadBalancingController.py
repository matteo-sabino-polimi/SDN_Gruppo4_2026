from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import set_ev_cls, CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event, switches
from ryu.topology.api import get_all_switch, get_all_link, get_all_host
from ryu.lib.packet import packet, ethernet, ether_types, arp
from ryu.lib import hub
import networkx as nx

# Si richiede l'uso del topology discovery: ryu-manager --observe-links

class HopByHopMonitoringSwitch(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(HopByHopMonitoringSwitch, self).__init__(*args, **kwargs)
        # Supponiamo, per semplicità, che la topologia sia fissa e non cadano collegamenti nella rete...

        # Inizializza il grafo diretto dei link.
        net = nx.DiGraph()
        # Popola il grafo e inizializza a zero il costo di ogni link (valutato in Byte/s)
        for link in get_all_link(self):
            net.add_edge(link.src.dpid, link.dst.dpid, port=link.src.port_no, weight=0)

        # TODO: come teniamo il conto della differenza tra i costi totali e parziali?

        # Genera il thread che esegue self._monitor.
        self.monitor_thread = hub.spawn(self._monitor)

    def _monitor(self):
        # Ogni 5s...
        while True:
            # ...per ogni switch della topologia lancia self._request_stats.
            for datapath in self.datapaths.values():
                self._request_stats(datapath)
            hub.sleep(5)

    def _request_stats(self, datapath):
        # Manda le richieste del framework di Ryu.
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        # richiesta per le FlowStats
        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)
        # richiesta per le PortStats
        req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        # Switch CONFIG classico.
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=0, match=match, instructions=inst)
        datapath.send_msg(mod)

    def find_destination_switch(self, destination_mac):
        # Per ogni host della topologia si cerca di matchare il MAC di destinazione.
        for host in get_all_host(self):
            # Se l' host viene trovato si restituisce l'associazione...
            if host.mac == destination_mac:
                return (host.port.dpid, host.port.port_no)
        # ...altrimenti si restituisce un'associazione vuota.
        return (None, None)

    def find_next_hop_to_destination(self, source_id, destination_id):
        path = nx.dijkstra_path(self.net, source_id, destination_id)
        first_link = self.net[path[0]][path[1]]

        return first_link['port']

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        # Se il pacchetto è ARP esegui il proxy arp.
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            self.proxy_arp(msg)
            return

        # Se il pacchetto non è IPv4 (ARP ricade nel caso ma è precedentemente gestito) ignoralo.
        if eth.ethertype != ether_types.ETH_TYPE_IP:
            self.logger.info('Non-IPv4 package received: ignored')
            return

        destination_mac = eth.dst

        # Cerca lo switch di destinazione...
        (dst_dpid, dst_port) = self.find_destination_switch(destination_mac)

        # ...se l'host non è stato trovato, ignora il pacchetto.
        if dst_dpid is None or dst_port is None:
            self.logger.info('Unknown host: ignored')
            return

        # ...se l'host è direttamente raggiungibile, la porta di uscita verso l'host è impostata,
        if dst_dpid == datapath.id:
            output_port = dst_port
        # altrimenti cerca il next hop veso la destinazione.
        else:
            output_port = self.find_next_hop_to_destination(datapath.id,dst_dpid)

        # Inoltra il pacchetto.
        actions = [parser.OFPActionOutput(output_port)]
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port, actions=actions, data=msg.data)
        datapath.send_msg(out)

        # Aggiungi la Regola.
        match = parser.OFPMatch(eth_dst=destination_mac)
        actions = [parser.OFPActionOutput(output_port)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=10, match=match, idle_timeout=20 , instructions=inst, buffer_id=msg.buffer_id)
        datapath.send_msg(mod)

        return

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        # TODO: quali stats vogliamo utilizzare?
        body = ev.msg.body
        self.logger.info('datapath         '
                         'match        '
                         'out-port packets bytes')
        self.logger.info('---------------- '
                         '-------- ----------------- '
                         '-------- -------- --------')
        for stat in body:
            self.logger.info('%016x %26s %8x %8d %8d',
                             ev.msg.datapath.id,
                             stat.match,
                             stat.instructions[0].actions[0].port, stat.packet_count, stat.byte_count)
            # TODO: torvare un modo per estrarre "dst.datapathid" e aggiornare i pesi.
            self.net.add_edge(ev.msg.datapath.id, port=stat.port_no, )

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        # TODO: quali stats vogliamo utilizzare?
        body = ev.msg.body
        self.logger.info('datapath port '
                         'rx-pkts rx-bytes rx-error '
                         'tx-pkts tx-bytes tx-error')
        self.logger.info('---------------- -------- '
                         '-------- -------- -------- '
                         '-------- -------- --------')
        for stat in body:
            self.logger.info('%016x %8x %8d %8d %8d %8d %8d %8d',
                             ev.msg.datapath.id, stat.port_no,
                             stat.rx_packets, stat.rx_bytes, stat.rx_errors,
                             stat.tx_packets, stat.tx_bytes, stat.tx_errors)

    # Rippato dalle soluzioni del lab.
    def proxy_arp(self, msg):
        # TODO: guardati come funziona.
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt_in = packet.Packet(msg.data)
        eth_in = pkt_in.get_protocol(ethernet.ethernet)
        arp_in = pkt_in.get_protocol(arp.arp)

        # gestiamo solo i pacchetti ARP REQUEST
        if arp_in.opcode != arp.ARP_REQUEST:
            return

        destination_host_mac = None

        for host in get_all_host(self):
            if arp_in.dst_ip in host.ipv4:
                destination_host_mac = host.mac
                break

        # host non trovato
        if destination_host_mac is None:
            return

        pkt_out = packet.Packet()
        eth_out = ethernet.ethernet(
            dst = eth_in.src,
            src = destination_host_mac,
            ethertype = ether_types.ETH_TYPE_ARP
        )
        arp_out = arp.arp(
            opcode  = arp.ARP_REPLY,
            src_mac = destination_host_mac,
            src_ip  = arp_in.dst_ip,
            dst_mac = arp_in.src_mac,
            dst_ip  = arp_in.src_ip
        )
        pkt_out.add_protocol(eth_out)
        pkt_out.add_protocol(arp_out)
        pkt_out.serialize()

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=ofproto.OFPP_CONTROLLER,
            actions=[parser.OFPActionOutput(in_port)],
            data=pkt_out.data
        )
        datapath.send_msg(out)
        return