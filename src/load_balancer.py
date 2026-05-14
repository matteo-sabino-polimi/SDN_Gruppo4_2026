# Possible topology in mininet 
#   --arp NOT required, ARP PROXY INSTALLED (FLOODING NOT POSSIBLE, hosts are discovered only if they talked before)
#       (pingall suggested at startup to discover all hosts)
#
#   --mac to use incremental values for the mac addesses (Optional but usefull)
# sudo mn --mac torus,3,3 --controller-remote

# sudo mn --mac --custom topo_load_balancer.py --topo LBTopo --controller remote


# Use the following commands to run the test
# ryu-manager --observe-link load_balancer.py flowmanager/flowmanager.py

# localhost:8080/home/index.html to use flowmanager 

from operator import attrgetter
from ryu.app import simple_switch_13
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER, CONFIG_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
from ryu.base import app_manager
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event, switches
from ryu.topology.api import get_all_switch, get_all_link, get_all_host # import to know the topology of the network
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.lib.packet import arp
import networkx as nx # library for graphs's algorithms

from math import log1p
import time

# to draw the possible network design
# import matplotlib.pyplot as plt

TIME_INTERVAL = 10 # in seconds
ALPHA = 0.1 # weight given to new data (For the Exponentially Weighted Moving Average filter)

class LoadBalancer(app_manager.RyuApp):
    OFP_VERSION = [ofproto_v1_3.OFP_VERSION] # version we want to manage

    def __init__(self, *args, **kwargs):
        super(LoadBalancer, self).__init__(*args, **kwargs)

        # datapath table
        self.datapaths = {}

        # graph of the network
        self.graph = nx.DiGraph()
        
        # dictionary of port stats for each datapath
        self.port_stats = {}
        
        self.last_bytes = {}
        self.last_time = {}
        
        # exponentially weighted moving average (what is this?)
        self.ewma = {}
        
        self.alpha = ALPHA

        # thread that periodically monitors the links
        self.monitor_thread = hub.spawn(self._monitor)
    
    # manage connected switches
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)

    # event that is executed when a new switch connects/disconnects in the network
    @set_ev_cls(event.EventSwitchEnter)
    @set_ev_cls(event.EventSwitchLeave)
    def build_topology(self, ev):
        # graph cleared each time a new switch is connected/disconnected
        self.graph.clear()
        
        # add all known switches to the graph
        for switch in get_all_switch(self):
            self.graph.add_node(switch.dp.id)
            
        # generate all links between the switches
        for link in get_all_link(self):
            self.graph.add_edge(
                link.src.dpid,
                link.dst.dpid,
                port = link.src.port_no,
                weight = 1 # set starting weight for all edges
            )

    # basic rule is to send all packages to controller if no rule is found
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures) # we want to intercept the event SwitchFeatures, it is used to install the rules at the startup of the switch
    def switch_features_handler(self, ev): # ev are the parameters of the packet
        datapath = ev.msg.datapath # datapath is the id of the switch
        ofproto = datapath.ofproto # all the functions of of
        parser = datapath.ofproto_parser # function to create of messages
        
        # match all packets if empty
        match = parser.OFPMatch()
        
        # list of actions
        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER, # the action is to send the package to the controller
                ofproto.OFPCML_NO_BUFFER # send the whole package
            )
        ]  

        inst = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]

        # message to send
        mod = parser.OFPFlowMod(
            datapath = datapath,
            priority = 0, # lowest possible priority so that the other rules can be applied
            match = match,
            instructions = inst
        )

        # sent to the switch the mod
        datapath.send_msg(mod)

    ### AUXILLARY FUNCTIONS ###

    def _monitor(self):
        while True:
            for dp in self.datapaths.values():
                self._request_stats(dp) # for all switches send a request for stats
            hub.sleep(TIME_INTERVAL)

    def _request_stats(self, datapath):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)
        req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req)

    # find destination switch and switch port
    def find_destination_switch(self,destination_mac):
        for host in get_all_host(self):
            if host.mac == destination_mac:
                return (host.port.dpid, host.port.port_no)
        return (None,None)
    
    # find the path that the flow must follow
    def find_path(self, source_id, destination_id):
        net = self.graph
        try:
            path = nx.dijkstra_path(
                net,
                source_id,
                destination_id,
                weight='weight'
            )
            return path # returns a list of numbers
        
        except nx.NetworkXNoPath:
            self.logger.warning(f"No path between {source_id} and {destination_id}")
            return None
        
        except nx.NodeNotFound:
            self.logger.warning(f"Switch not found in topology graph")
            return None

    def get_edge_ports(self):
        """Trova tutte le porte degli switch che non sono collegate ad altri switch."""
        edge_ports = []
        
        # Recupera tutti gli switch e tutti i link (collegamenti tra switch)
        switches = get_all_switch(self)
        internal_links = get_all_link(self)

        # Crea un set di tutte le porte interne (switch-to-switch)
        internal_ports = set()
        for link in internal_links:
            internal_ports.add((link.src.dpid, link.src.port_no))
            internal_ports.add((link.dst.dpid, link.dst.port_no))

        # Trova le porte che non sono nel set delle porte interne
        for switch in switches:
            for port in switch.ports:
                # Escludiamo la porta LOCAL dello switch
                if port.port_no != switch.dp.ofproto.OFPP_LOCAL:
                    # Se la porta non è un collegamento tra switch, è una porta Edge
                    if (switch.dp.id, port.port_no) not in internal_ports:
                        edge_ports.append((switch.dp, port.port_no))
                        
        return edge_ports
        
    # define our own proxy arp
    def proxy_arp(self, msg):
        
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt_in = packet.Packet(msg.data)
        eth_in = pkt_in.get_protocol(ethernet.ethernet)
        arp_in = pkt_in.get_protocol(arp.arp)

        # ARP REQUEST messages are the only ones managed by this function
        if arp_in.opcode != arp.ARP_REQUEST:
            return

        destination_host_mac = None

        # trying to find the host that the message is looking for
        for host in get_all_host(self):
            if host.ipv4 and arp_in.dst_ip in host.ipv4: # checks if dst_ip is in host.ipv4 only if host.ipv4 is not Non and if is not empty
                destination_host_mac = host.mac
                break
        
        if destination_host_mac is None:
            self.logger.info(f"Host {arp_in.dst_ip} sconosciuto. Eseguo Smart Edge Flooding.")
            
            # Recuperiamo tutte le porte rivolte verso gli host
            edge_ports = self.get_edge_ports()
            
            for dp, edge_port_no in edge_ports:
                # Evitiamo di rimandare la richiesta ARP indietro da dove è arrivata
                if dp.id == datapath.id and edge_port_no == in_port:
                    continue

                # Creiamo l'azione per inviare il pacchetto su quella specifica porta Edge
                actions = [parser.OFPActionOutput(edge_port_no)]
                
                # Impacchettiamo l'ARP originario e lo spariamo fuori
                out = parser.OFPPacketOut(
                    datapath=dp,
                    buffer_id=ofproto.OFP_NO_BUFFER,
                    in_port=ofproto.OFPP_CONTROLLER,
                    actions=actions,
                    data=msg.data
                )
                dp.send_msg(out)
            return

        # if host is not found drop the packet
        if destination_host_mac is None:
            return

        assert destination_host_mac is not None

        # if the host is found an ARP REPLY is sent back
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

        # the ARP REPLY is sent back
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=ofproto.OFPP_CONTROLLER,
            actions=[parser.OFPActionOutput(in_port)],
            data=pkt_out.data
        )

        datapath.send_msg(out)
        return


    # packet in management
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        
        # extract the message
        msg = ev.msg
        datapath = msg.datapath # id of the switch
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port'] # in port from which the package arrived

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        # if an ARP packet is sent it is managed with the proxy arp
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            self.proxy_arp(msg)
            return # the rules are not installed when doing the arp request

        # ignore all LLDP packets so that ryu can manage them and populate get_all_links
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        # ignore all non IPv4 packets (es. ARP, LLDP)
        if eth.ethertype != ether_types.ETH_TYPE_IP:
            return
        
        destination_mac = eth.dst

        # find destination switch
        (dst_dpid, dst_port) = self.find_destination_switch(destination_mac) # find last switch and it's port connected to destination host

        if dst_dpid is None:
            self.logger.warning(f"Destination host not found")
            return
        
        path = self.find_path(datapath.id, dst_dpid)
        
        if path is None:
            self.logger.warning(f"No path found")
            return

        if len(path) == 1:
            # used if host is directly connected
            output_port = dst_port
        else:
            # used if host is not directly connected
            output_port = self.graph[path[0]][path[1]]['port'] # output_port is the port that connects the first switch on the path to the second one 

        # 1. the received packet is sent to correct port (I send it manually because if it arrives before the installation of the rule it is sent back to me)
        
        actions = [parser.OFPActionOutput(output_port)]

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data
        )

        datapath.send_msg(out)

        # 2. add rule to all switches on the path (except last switch)
        
        for i in range(len(path) - 1):
            
            current_switch = path[i] 
            next_switch = path[i + 1]
            
            current_switch_output_port = self.graph[current_switch][next_switch]['port']
            
            current_datapath = self.datapaths.get(current_switch)
            
            if current_datapath is None:
                self.logger.warning(f"Datapath {current_switch} not found")
                return
            
            # technically ofproto and parser can change for each switch
            ofproto = current_datapath.ofproto
            parser = current_datapath.ofproto_parser
            
            match = parser.OFPMatch(
                eth_src = eth.src,
                eth_dst = destination_mac
            )
            
            actions = [parser.OFPActionOutput(current_switch_output_port)]
            
            inst = [
                parser.OFPInstructionActions(
                    ofproto.OFPIT_APPLY_ACTIONS,
                    actions
                )
            ]
            
            mod = parser.OFPFlowMod(
                datapath = current_datapath, # send the mod to the switch in the list selected
                priority = 10,
                match = match,
                idle_timeout = TIME_INTERVAL,
                instructions = inst
            )
            current_datapath.send_msg(mod) # send the mod to the correct switch

        # 3. install the rule on the last switch
        
        last_switch = path[-1]
        last_datapath = self.datapaths.get(last_switch)
        
        if last_datapath is None:
            self.logger.warning(f"Datapath {last_switch} not found")
            return

        # technically ofproto and parser can change for each switch
        ofproto = last_datapath.ofproto
        parser = last_datapath.ofproto_parser
        
        match = parser.OFPMatch(
                eth_src = eth.src,
                eth_dst = destination_mac
            )
        
        actions = [parser.OFPActionOutput(dst_port)]
            
        inst = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]
        
        mod = parser.OFPFlowMod(
            datapath = last_datapath,
            priority = 10,
            match = match,
            idle_timeout = TIME_INTERVAL,
            instructions = inst
        )
        
        last_datapath.send_msg(mod)
        
        return

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
                stat.instructions[0].actions[0].port,
                stat.packet_count, stat.byte_count)
        return

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        
        body = ev.msg.body
        
        datapath_id = ev.msg.datapath.id
        
        self.logger.info('datapath port '
        'rx-pkts rx-bytes rx-error '
        'tx-pkts tx-bytes tx-error')
        self.logger.info('---------------- -------- '
        '-------- -------- -------- '
        '-------- -------- --------')
        
        for stat in body:
            
            # logging function for statistics
            self._log_port_statistics(datapath_id, stat)
            
            bandwidth_usage = self._compute_bandwidth_usage(
                datapath_id,
                stat.port_no,
                stat.tx_bytes
                )
            
            if bandwidth_usage is not None:
                self._update_link_weight(
                    datapath_id,
                    stat.port_no,
                    bandwidth_usage
                )
            
        return

    def _log_port_statistics(self, dpid, stat):
        self.logger.info('%016x %8x %8d %8d %8d %8d %8d %8d',
                dpid, stat.port_no,
                stat.rx_packets, stat.rx_bytes, stat.rx_errors,
                stat.tx_packets, stat.tx_bytes, stat.tx_errors) 
        return
    
    def _compute_bandwidth_usage(self, dpid, port_no, current_tx_bytes):
        
        now = time.monotonic()
        
        switch_port_id = (dpid, port_no)
        
        last_bytes = self.last_bytes.get(switch_port_id)
        last_time = self.last_time.get(switch_port_id)
        
        if last_bytes is None or last_time is None:
            self.last_bytes[switch_port_id] = current_tx_bytes
            self.last_time[switch_port_id] = now
            return None
        
        bytes_diff = current_tx_bytes - last_bytes
        
        dt = now - last_time
        
        # update stored values and time 
        self.last_bytes[switch_port_id] = current_tx_bytes
        self.last_time[switch_port_id] = now
        
        if dt <= 0:
            return None
        
        if bytes_diff < 0:
            return None
        
        bandwidth_usage = bytes_diff / dt
        
        previous_ewma = self.ewma.get(switch_port_id)
        
        if previous_ewma is None:
            self.ewma[switch_port_id] = bandwidth_usage
            return bandwidth_usage
        
        filtered = self.alpha * bandwidth_usage + (1-self.alpha) * previous_ewma # applying the EWMA filter
        self.ewma[switch_port_id] = filtered
        
        return filtered
    
    def _update_link_weight(self, dpid, port_no, bandwidth_usage):
        
        if dpid not in self.graph:
            return
        
        for neighbor_switch in self.graph[dpid]:
            
            edge = self.graph[dpid][neighbor_switch]
            reverse_edge = self.graph[neighbor_switch][dpid]
            
            if edge['port'] == port_no:
                edge['weight'] = 1 + bandwidth_usage # EWMA filter already applied, no need to use the log of the value
                reverse_edge['weight'] = 1 + bandwidth_usage
            
                self.logger.info(
                    f"link {dpid} --> {neighbor_switch} "
                    f"(port {port_no}) updated"
                    f"weight={edge['weight']}"
                )
                
                break # no need to look forward
            
        return