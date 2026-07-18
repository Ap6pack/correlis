# Initial Access Demo

A synthetic, non-operational scenario used to prove the Correlis contracts and
replay behavior.

Sequence:

1. Outrider identifies an internet-exposed vulnerable application.
2. Suricata observes reconnaissance.
3. Suricata observes an exploit attempt.
4. Sysmon records suspicious server-side process execution.
5. The process establishes an outbound connection.
6. The compromised server authenticates to a database server.
7. A service identity accesses restricted data.

All addresses and domains use documentation or reserved example ranges. No event
represents a real organization or compromise.
