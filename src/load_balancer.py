# Possible topology in mininet 
#   --arp not required, ARP PROXY INSTALLED
#   --mac to use incremental values for the mac addesses
# sudo mn --mac torus,3,3 --controller-remote


# Use the following commands to run the test
# ryu_flowmanager --observe-link load_balancer.py flowmanager/flowmanager.py

# localhost:8080/home/index.html to use flowmanager 

from operator import attrgetter
from ryu.app import simple_switch_13
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER, CONFIG_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
from ryu.base import app_manager
from ryu.ofproto import ofproto_V1_3
from ryu.topology import event, switches
from ryu.topology.api import get_all_switch, get_all_link, get_all_host # import to know the topology of the network
from ryu.lib.packet import packet, ethernet, ether_types
import networkx as nx # library for graphs's algorithms

# to draw the possible network design
import matplotlib.pyplto as plt 

class LoadBalancer(app_manager.RyuApp):
    OFP_VERSION = [ofproto_v1_3.OFP_VERSION] # version we want to manage

    def __init__(self, *args, **kwargs):
        super(PsrSwitch, self).__init__(*args, **kwargs)
        self.mac_to_port = {} # empty dictionary

    # basic rule is to send all packages to controller if no rule is found
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures) # we want to intercept the event SwitchFeatures, it is used to install the rules at the startup of the switch
    def switch_features_handler(self, ev): # ev are the parameters of the packet
        datapath = ev.msg.datapath # datapath is the id of the switch
        ofproto = datapath.ofproto # all the functions of of
        parser = datapath.pfproto_parser # function to create of messages
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
            math = match,
            instructions = inst
        )

        # sent to the switch the mod
        datapath.send_msg(mod)

    ### AUXILLARY FUNCTIONS ###

    # find destination switch and switch port
    def find_destination_switch(self,destination_mac):
        for host in get_all_host(self):
            if host.mac == destination_mac:
                return (host.port.dpid, host.port.port_no)
        return (None,None)

    # find the next switch to which the package has to be sent
    def find_next_hop_to_destination(self,source_id,destination_id):
        net = nx.DiGraph()
        for link in get_all_link(self):
            net.add_edge(link.src.dpid, link.dst.dpid, port=link.src.port_no)

        path = nx.shortest_path(
            net,
            source_id,
            destination_id
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

        # ARP REQUEST messages are the only ones managed by this function and we check so
        if arp_in.opcode != arp.ARP_REQUEST:
            return

        destination_host_mac = None

        # treying to find the host that the message is looking for
        for host in get_all_host(self):
            if arp_in.dst_ip in host.ipv4:
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
    @set_ev_class(ofp_event.EventOFPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        # extract the message
        msg = ev.msg
        datapath = msg.datapath # id of thw switch
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port'] # in port from which the package arrived

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        # if an ARP packet is sent it is managed with the proxy arp
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            self.proxy_arp(msg)
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
        
        actions = [parser.OFPactionOutput(output_port)]

        out = parser.OFPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data
        )

        datapath.send_msg(out)

        # add rule for the next packets
        
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
            instructions=inst
            )
        
        # send the rule to the switch
        datapath.send_msg(mod)

        return


