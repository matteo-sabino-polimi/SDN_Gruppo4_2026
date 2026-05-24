# SDN_Gruppo4_2026
## Progetto 4. Load Balancing (least load)
Scrivere un programma Ryu che:
- Monitori il carico su ogni link.
- Per ogni nuova connessione scelga il percorso a costo minimo dando un peso minore ai collegamenti scarichi.
- Carichi sullo switch una regola per l'instradamento senza più passare dal controllore.

---

## 🧪 Workflow Sperimentale (Laboratorio)
In laboratorio abbiamo utilizzato una topologia reale composta da Switch Open vSwitch e Raspberry Pi (RPi4 e RPi5). Il seguente diagramma illustra i passaggi eseguiti per validare il bilanciamento del carico:

```mermaid
graph TD
    Start((Inizio Test)) --> Ping["1. Discovery: Ping tra RPi4 e RPi5"]
    
    Ping --> IperfServer["2. RPi4 (Server):<br/>iperf -s -i 1 -p 9001 &<br/>iperf -s -i 1 -p 9002 &"]
    
    IperfServer --> Client1["3. RPi5 (Client - Flusso 1):<br/>iperf -c 10.10.6.45 -p 9001 -b 10M &"]
    
    Client1 --> CheckFlow1["4. Verifica Flow Tables:<br/>Il traffico segue lo Shortest Path"]
    
    CheckFlow1 --> Client2["5. RPi5 (Client - Flusso 2):<br/>iperf -c 10.10.6.45 -p 9002 -b 10M &"]
    
    Client2 --> CheckFlow2["6. Verifica Bilanciamento:<br/>Nuova regola su Percorso Alternativo<br/>(Link più libero)"]
    
    CheckFlow2 --> End((Test Concluso))

    style CheckFlow2 fill:#f96,stroke:#333,stroke-width:2px
```

### Comandi eseguiti sugli Host:
- **RPi4 (Server):**
  ```bash
  iperf -s -i 1 -p 9001 &
  iperf -s -i 1 -p 9002 &
  ```
- **RPi5 (Client):**
  ```bash
  # Primo flusso (percorso breve)
  iperf -c 10.10.6.45 -p 9001 -b 10M &
  
  # Secondo flusso (percorso alternativo per carico)
  iperf -c 10.10.6.45 -p 9002 -b 10M &
  ```

---

## 💻 Ambiente Simulato (Mininet)
Se si desidera testare il progetto in un ambiente simulato, utilizzare i seguenti comandi.

### Setup della rete
Comando standard:
```bash
sudo mn --mac torus,3,3 --controller-remote
```
Rete custom (simile al laboratorio):
```bash
sudo mn --mac --custom topo_load_balancer.py --topo LBTopo --controller remote
```
Rete custom con link a capacità predefinita [100Mb/s]:
```bash
sudo mn --mac --custom topo_load_balancer.py --topo LBTopo --controller remote --link tc,bw=100
```
Rete custom che simula le condizioni del laboratorio:
```bash
sudo mn --mac --custom topo_simulated_lab.py --topo LBTopo --controller remote
```

---

## 🛠️ Esecuzione del Controller
Per avviare il load balancer insieme al gestore dei flussi:
```bash
ryu-manager --observe-link load_balancer.py flowmanager/flowmanager.py
```

### Monitoring GUI
L'interfaccia grafica per monitorare i flussi in tempo reale è disponibile a:
[http://localhost:8080/home/index.html](http://localhost:8080/home/index.html)

---

## 👥 Authors
- Matteo
- Pietro
- Walter
- Taddeo
