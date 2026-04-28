# SDN_Gruppo4_2026
## Progetto 4. Load Balancing (least load)
Scrivere un programma Ryu che:
- Monitori il carico su ogni link
- Per ogni nuova connessione scelga il percorso a costo minimo dando un peso minore ai
collegamenti scarichi
- Carichi sullo switch una regola per l'instradamento senza più passare dal controllore.

## Comandi per il setup
Comando per il setup della rete simulata in mininet
``` bash
sudo mn --mac torus,3,3 --controller-remote

```
In caso di rete custom
``` bash
sudo mn --mac --custom topo_load_balancer.py --topo LBTopo --controller remote
```

Comando per il setup di flowmanager
``` bash
ryu-manager --observe-link load_balancer.py flowmanager/flowmanager.py
```

Link per il monitoring tramite interfaccia grafica
[http://localhost:8080/home/index.html](http://localhost:8080/home/index.html)

## Authors
- Matteo
- Pietro
- Walter
- Taddeo
