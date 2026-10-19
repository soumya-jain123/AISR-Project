# Project Team Meeting Logs

*Mandatory for Report Appendix as per Course Guidelines (CSAI4014 AISR).*

---

## Meeting 1: Kickoff, Architecture & Protocol Design
- **Date:** 19 October 2026
- **Duration:** 45 minutes
- **Attendees:** All team members
- **Agenda:** 
  1. Review course guidelines and project requirements.
  2. Select communication protocol (TCP, newline-delimited JSON).
  3. Divide responsibilities across modules M0 through M6.
- **Decisions Made:**
  - Transport: TCP localhost socket communication between Client and Server.
  - Heartbeat: 0.5 s transmission interval, 1.5 s server timeout.
  - Safety Priority: E-stop overrides all commands and latches until explicit reset.
  - Testing: Implement MockDriver first to allow parallel development before CoppeliaSim integration.

---

## Meeting 2: Core State Machine & Client Implementation
- **Date:** 21 October 2026 (Scheduled)
- **Duration:** 40 minutes
- **Agenda:** Review M1 protocol unit test results, M2 server states, and M4 terminal UI.
- **Decisions Made:** [To be recorded by team]

---

## Meeting 3: Simulator Integration & Benchmark Sweeps
- **Date:** 24 October 2026 (Scheduled)
- **Duration:** 60 minutes
- **Agenda:** Validate Pioneer P3-DX inside CoppeliaSim; run 0–1000 ms delay sweeps.
- **Decisions Made:** [To be recorded by team]

---

## Meeting 4: Report Writing, Slides & Video Rehearsal
- **Date:** 27 October 2026 (Scheduled)
- **Duration:** 60 minutes
- **Agenda:** Assemble report chapters, check 17-slide limit, rehearse 20-min video with live demo.
- **Decisions Made:** [To be recorded by team]
