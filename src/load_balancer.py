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

# to draw the possible network design
# import matplotlib.pyplot as plt

TIME_INTERVAL = 10 # in seconds

class LoadBalancer(app_manager.RyuApp):
    OFP_VERSION = [ofproto_v1_3.OFP_VERSION] # version we want to manage

    def __init__(self, *args, **kwargs):
        super(LoadBalancer, self).__init__(*args, **kwargs)
        self.mac_to_port = {} # empty dictionary

         # datapath table
        self.datapaths = {}

        # graph of the network
        self.graph = nx.DiGraph()
        self.port_stats = {} # 
        
        

        # thread che lancia periodicamente le richieste
        self.monitor_thread = hub.spawn(self._monitor)

    # basic rule is to send all packages to controller if no rule is found
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures) # we want to intercept the event SwitchFeatures, it is used to install the rules at the startup of the switch
    def switch_features_handler(self, ev): # ev are the parameters of the packet
        datapath = ev.msg.datapath # datapath is the id of the switch
        ofproto = datapath.ofproto # all the functions of of
        parser = datapath.ofproto_parser # function to create of messages
        self.mac_to_port[datapath.id] = {}
        
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

    # find the next switch to which the package has to be sent
    def find_next_hop_to_destination(self,source_id,destination_id):
        net = self.graph
        for link in get_all_link(self):
            net.add_edge(link.src.dpid, link.dst.dpid, port=link.src.port_no)

        path = nx.dijkstra_path(
            net,
            source_id,
            destination_id,
            weight='weight'
        )

        first_link = net[ path[0] ][ path[1] ]

        return first_link['port']
    
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
        (dst_dpid, dst_port) = self.find_destination_switch(destination_mac)

        # host not found
        if dst_dpid is None:
            # print "DP: ", datapath.id, "Host not found: ", pkt_ip.dst
            return

        if dst_dpid == datapath.id:
            # used if host is directly connected
            output_port = dst_port    
        else:
            # used if host is not directly connected
            output_port = self.find_next_hop_to_destination(datapath.id,dst_dpid)

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

        # 2. add rule for the next packets
        
        match = parser.OFPMatch(
            eth_dst=destination_mac
            )
        
        inst = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS,
                actions
            )
        ]
        
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=10,
            match=match,
            idle_timeout = TIME_INTERVAL, # interval of stats report
            instructions=inst
            )
        
        # send the rule to the switch
        datapath.send_msg(mod)

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
            
            current_tx_bytes = stat.tx_bytes
            dpid = ev.msg.datapath.id
            port_no = stat.port_no
            key = (dpid, port_no)

            if key in self.port_stats:
                previous_tx_bytes = self.port_stats[key]

                bytes_diff = current_tx_bytes - previous_tx_bytes

                bandwith_usage = bytes_diff / TIME_INTERVAL

             # link adjourned
            if dpid in self.graph:
                for link in self.graph[dpid]:
                    if self.graph[dpid][link]['port'] == port_no:
                        self.graph[dpid][link]['weight'] = bandwith_usage
                        self.logger.info(f"link {dpid} -> {link} (port {port_no} adjourned {bandwith_usage} B/s)")
                        break

            self.port_stats[key] = current_tx_bytes
        return

