#! /usr/bin/python
# mn --custom topo_simulated.py --topo LBTopo

from mininet.topo import Topo   

""" h3          h2
      \        /
       s5 -- s4
      /        \
     s6        s3
      \        /
       s1 -- s2
      /
    h1
"""

class LBTopo ( Topo ):

  def build(self):

    # Add hosts
    host1= self.addHost('h1')
    host2= self.addHost('h2')
    host3= self.addHost('h3')

    # Add switches
    switch1=self.addSwitch('s1')
    switch2=self.addSwitch('s2')
    switch3=self.addSwitch('s3')
    switch4=self.addSwitch('s4')
    switch5=self.addSwitch('s5')
    switch6=self.addSwitch('s6')

    # Add links for all switches
    # switch1
    self.addLink(switch1, host1)
    self.addLink(switch1, switch2)
    self.addLink(switch1, switch4)
    self.addLink(switch1, switch6)

    # switch2
    self.addLink(switch2, switch1)
    self.addLink(switch2, switch3)
    
    # switch3
    self.addLink(switch3, switch2)
    self.addLink(switch3, switch4)
    
    # switch4
    self.addLink(switch4, host2)
    self.addLink(switch4, switch1)
    self.addLink(switch4, switch3)
    self.addLink(switch4, switch5)
    
    # switch5
    self.addLink(switch5, host3)
    self.addLink(switch5, switch4)
    self.addLink(switch5, switch6)
    
    # switch6
    self.addLink(switch6, switch1)
    self.addLink(switch6, switch5)

topos = { 'LBTopo' : ( lambda: LBTopo() ) }
