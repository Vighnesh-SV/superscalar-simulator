# Superscalar Scheduling Simulator

**Live demo:** https://vighnesh-sv.github.io/superscalar-simulator/

Computer Architecture and Organization project (Unit 3: Pipeline Processing).

A cycle-by-cycle simulator of a superscalar processor. It takes a short assembly program, finds data dependencies (RAW, WAR, WAW) and resource conflicts, schedules the instructions on multiple execution units, and compares single-issue with multi-issue execution (total cycles, IPC, utilization, speedup).

## Run it

Open `index.html` in any web browser. No installation or server needed.

Live version: https://claude.ai/artifact/FRTp5aDL1iseGVdUUEmYxv

## Features

- Live animated pipeline: IF, ID, IS (issue queue), EX (ALU, multiplier, load/store, branch units), WB
- Hazard detection with the exact reason for every stall
- Hazard solutions you can switch on and off: data forwarding, register renaming, out-of-order issue, pipelined units, branch prediction (stall, predict not-taken, 2-bit predictor with flush)
- Timing (space-time) diagram, dependency graph, hazard table
- Performance comparison of 1, 2, 4 and 8-issue execution and of each hazard solution

## Tech stack

HTML5, CSS3 and vanilla JavaScript in one file. Animations use the Web Animations API and the dependency graph uses inline SVG. No frameworks and no backend.

## Planned features

Planned features are tracked in a separate document shared with the team.
