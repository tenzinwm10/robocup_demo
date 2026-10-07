# T2 communications and referee oversight

Start the read-only dashboard on the workstation:

```bash
bash workstation.sh oversight
```

Open http://127.0.0.1:8768. The communications observer starts automatically with the brains. The dashboard can stay open while matches are stopped; it labels absent/stale data explicitly. `bash workstation.sh oversight-stop` stops the web UI.

You can inspect team-channel packets and bytes per wall second, source/destination ports, payload size, CRC/authentication failures, sequence gaps/reordering/duplicates and robot reboots. It also shows reported leader/ball owner/roles, the brain's teammate liveness, correlated RPC replies/latency/native status codes, unmatched replies and requests unanswered after 10 seconds.

GameController views show state, set play, kicking team, score, half, clocks, packet number/gaps, budgets, player penalties/cautions and recent transitions. Raw Studio v19 and translated T2 v20 messages are available side by side, with bounded NDJSON event logs for later inspection. Original RGrt v4 return packets on UDP 3939 are separate from the team channel.

Current branch facts:

| Item | Value / behavior |
|---|---|
| Tactical state | 2 Hz per robot in the supplied profile |
| Discovery | 2 Hz per robot |
| Team channel destination | UDP 10000 + team ID, broadcast for both message kinds |
| Tactical state source | UDP 30000 + team ID × 20 + player ID |
| State / discovery size | 268 / 37 bytes, protocol v6 |
| Branch payload cap | 512 bytes |
| Tactical timeout | 600 ms configured, raised to 1600 ms at 2 Hz |
| Supported state rate | 0.1–20 Hz; restart after changing configuration |
| GameController return | 32 bytes, typically 1 Hz per robot |
| Brain budget throttling | Not implemented |
| Studio budget counter | Initialized to 12000; no traffic-decrement path in the inspected runtime |

The budget projection counts state plus discovery as observed datagrams and adjusts for simulation speed. Three robots at default rates emit roughly 12 packets per wall second: about 7200 packets for 600 match seconds at 1×, or 36000 at 0.2×. It is an estimate, not official competition accounting. Validate the competition's definition before using it as a compliance threshold.

The observer neither sends team packets nor binds robot UDP ports. It uses the original C++ decoder to check CRC and SipHash and never displays the key. It does not change communication policy or throttle packets. Packet capture proves local observation, not receiver acceptance or end-to-end loss; the brain's own receiver-liveness report is shown separately.

Logs are under `logs/workstation/oversight/`. RPC logs retain mode/get-up/kick requests and native error replies; high-rate successful motion traffic is summarized in counters. Each event log retains three generations of up to 20 MB; queue overflow is counted. The dashboard is localhost-only and has no robot-control endpoints.
