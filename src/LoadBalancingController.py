# Topology discovery required: ryu-manager --observe-links

from ryu.base import app_manager
from ryu.controller import ofp_event, event
from ryu.controller.handler import set_ev_cls, CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.ofproto import ofproto_v1_3
from ryu.topology.api import get_all_link, get_all_host
from ryu.lib.packet import packet, ethernet, ether_types, arp
from ryu.lib import hub
import networkx as nx

MONITORING_TIME = 5 # [s] monitoring_time
BIT_RATE = 1 # [bps] bit_rate

class HopByHopMonitoringSwitch(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(HopByHopMonitoringSwitch, self).__init__(*args, **kwargs)
        # Suppose, for the sake of simplicity, that we are working with a fixed and immutable topology...

        self.datapaths = {}
        # Allocate two direct graphs for links and .
        self.network_graph = nx.DiGraph()
        self.build_topology()
        for link in get_all_link(self):
            self.network_graph.add_edge(link.src.dpid, link.dst.dpid, port=link.src.port_no,
                                        weight=0, previous_traffic=0)
        # Generate the thread executing self._monitor.
        self.monitor_thread = hub.spawn(self._monitor)

    def _monitor(self):
        # Every MONITORING_TIME s...
        while True:
            # ...for each switch of topology execute self._request_stats.
            for datapath in self.datapaths.values():
                self._request_stats(datapath)
            hub.sleep(MONITORING_TIME)

    def _request_stats(self, datapath):
        # Send requests of Ryu framework.
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        # request FlowStats
        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)
        # request PortStats
        req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)
            self.build_topology()

    # event that is executed when a new switch connects in the network
    @set_ev_cls(event.EventSwitchEnter)
    def build_topology(self):
        # Clear self.network_graph.
        self.network_graph.clear()
        # Fill, initialize to zero the weight {((Bytes(t)-Bytes(t0))/MONITORING_TIME)/(BIT_RATE/8)} of every edge
        # and initialize to zero the previous_traffic {Bytes(t0)}
        for link in get_all_link(self):
            self.network_graph.add_edge(link.src.dpid, link.dst.dpid, port=link.src.port_no,
                                        weight=0, previous_traffic=0)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        # Classic switch CONFIG.
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=0, match=match, instructions=inst)
        datapath.send_msg(mod)

    def find_destination_switch(self, destination_mac):
        # For each host of topology try to match host.mac and destination_mac.
        for host in get_all_host(self):
            # If host is found return tuple...
            if host.mac == destination_mac:
                return (host.port.dpid, host.port.port_no)
        # ...else return None tuple.
        return (None, None)

    def find_next_hop_to_destination(self, source_id, destination_id):
        path = nx.dijkstra_path(self.network_graph, source_id, destination_id)
        first_link = self.network_graph[path[0]][path[1]]

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

        # If the packet is an ARP packet, execute the proxy arp and ignore it.
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            self.proxy_arp(msg)
            return

        # If the packet is an LLDP packet, ignore it.
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        # If the packet isn't IPv4, ignore it.
        if eth.ethertype != ether_types.ETH_TYPE_IP:
            self.logger.info('Non-IPv4 package received: ignored')
            return

        destination_mac = eth.dst

        # Find destination switch.
        (dst_dpid, dst_port) = self.find_destination_switch(destination_mac)

        # If host was not found, ignore the packet...
        if dst_dpid is None or dst_port is None:
            self.logger.info('Unknown host: ignored')
            return

        # ...else, if host is directly connected the output_port is set to dst_port...
        if dst_dpid == datapath.id:
            output_port = dst_port
        # else find next_hop_to_destination.
        else:
            output_port = self.find_next_hop_to_destination(datapath.id,dst_dpid)

        # send the package.
        actions = [parser.OFPActionOutput(output_port)]
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port, actions=actions, data=msg.data)
        datapath.send_msg(out)

        # Add the new entry.
        match = parser.OFPMatch(eth_dst=destination_mac)
        actions = [parser.OFPActionOutput(output_port)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=10, match=match, idle_timeout=20 , instructions=inst, buffer_id=msg.buffer_id)
        datapath.send_msg(mod)

        return

    def get_dst_dpid_by_port_no(self, src_dpid, port_no):
        # For each neighbor of src_dpid...
        for dst_dpid, attributes in self.network_graph[src_dpid].items():
            # ...check if attributes.get('port') matches port_no.
            if attributes.get('port') == port_no:
                return dst_dpid
        # Return None if match wasn't found.
        return None

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
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

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
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
            src_id = stat.ev.msg.datapath.id
            stat_port = stat.port_no
            dst_dpid = self.get_dst_dpid_by_port_no(ev.msg.datapath.id, stat_port)
            if dst_dpid is not None:
                previous_traffic = self.network_graph[src_id][dst_dpid]['previous_traffic']
                self.network_graph.add_edge(ev.msg.datapath.id, dst_dpid, port=stat_port,
                                            weight=(((stat.rx_bytes - previous_traffic)/MONITORING_TIME)/(BIT_RATE/8)),
                                            previous_traffic=stat.rx_bytes)

    def proxy_arp(self, msg):
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt_in = packet.Packet(msg.data)
        eth_in = pkt_in.get_protocol(ethernet.ethernet)
        arp_in = pkt_in.get_protocol(arp.arp)

        # If the packet isn't ARP_REQUEST ignore it.
        if arp_in.opcode != arp.ARP_REQUEST:
            return

        destination_host_mac = None

        # Find destination switch.
        for host in get_all_host(self):
            if arp_in.dst_ip in host.ipv4:
                destination_host_mac = host.mac
                break

        # If host was not found, ignore the packet...
        if destination_host_mac is None:
            return

        # ...else manage ARP_REPLY.
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