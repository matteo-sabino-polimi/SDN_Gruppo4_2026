#! /usr/bin/python
# invocare con mn --custom load-balance-topology.py --topo LBTopo

from mininet.topo import Topo   #definire la topologia

"""h1 -- s1 -- s3 -- s4
         /   \      /
      h3/     \ s2 /
              /
           h2/
"""
class LBTopo ( Topo ):

  def build(self):

    #Aggiungo nuovi host
    host1= self.addHost('h1')
    host2= self.addHost('h2')
    host3= self.addHost('h3')

    #Aggiungo nuovi switch
    switch1=self.addSwitch('s1')
    switch2=self.addSwitch('s2')
    switch3=self.addSwitch('s3')
    switch4=self.addSwitch('s4')

    #Aggiungo i link agli switch
    #switch1
    self.addLink(switch1,host1)
    self.addLink(switch1,switch2)
    self.addLink(switch1,switch3)
    self.addLink(switch1,host3)

    # switch2
    self.addLink(switch2, switch1)
    self.addLink(switch2, switch4)
    self.addLink(switch2, host2)
    
    # switch4
    self.addLink(switch4, switch3)

topos = { 'LBTopo' : ( lambda: LBTopo() ) }
