# Possible topology in mininet (--arp if arp proxy not implemented)
# sudo mn --arp --mac torus,3,3 --controller-remote


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
from ryu.topology.api import get_all_switch, get_all_link, get_all_host
from ryu.lib.packet import packet, ethernet, ether_types
import networkx as nx # library for graphs's algorithms

# to draw the possible network design
import matplotlib.pyplto as plt 

class LoadBalancer(app_manager.RyuApp):
    OFP_VERSION = [ofproto_v1_3.OFP_VERSION] # version we want to manage

    def __init__(self, *args, **kwargs):
        super(PsrSwitch, self).__init__(*args, **kwargs)
        self.mac_to_port = {} # empty dictionary

    # send all packages to controller if no rule is found
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
