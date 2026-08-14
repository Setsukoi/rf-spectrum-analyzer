# What this project is

*rfsa* is a python program that talks to the lab spectrum analyzer **Keysight MXA N9020A** through LAN connection. It aims to change basic settings, take sweeps, read frequencies, save screenshots, and finally store all the data in a SQLite file *(measurements.db)*

It uses a repeatable script to replace clicking the front panel on the instrument and realize an automated process.

# How to connect with the instrument

There are three layers:
1. **VISA** - the Python API *(pyVISA)* that enables you to write or query with the instrument.
2. **SCPI** - the language that communicates with the instrument. 
3. **HisLIP** - the actual path on the LAN.

# What "rfsa" consists of

| Module | Role |
|:-----|:-----|
| models | Define basic common value objects |
| analyzer | Driver: validate numbers, send SCPI, wait for sweeps, read traces/markers/screens |
| storage | SQLite: one run (who / when / which box) and many sweeps (trace + settings + peak) |
| limits | Hardware limits |
| errors | Common types of errors defined in advance ｜

# Measurement flow

```bash
connect (HisLIP)
    → configure()     # center, span, RBW, atten, points — no sweep
    → capture()       # one sweep, then read trace 1
    → peak_search()   # marker on the highest point (power in dBm)
    → frequency counter   # finer Hz around that marker
    → save to measurements.db
    → screenshot
close
```

# How to use

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
pip install pyvisa-py    # when Keysight IO Libraries not available
```

## IP address setup

Ensure:
1. Analyzer and PC on the same link (this lab: PC 192.168.10.100, instrument 192.168.10.2).
2. On the analyzer, LAN services On (VXI-11 / Sockets / HisLIP).


## Commands

```bash
# Fake resource
.venv/bin/python examples/measure.py --fake
.venv/bin/python examples/measure.py --fake --frequency

# True instrument
.venv/bin/python examples/measure.py TCPIP0::192.168.10.2::hislip0::INSTR
```

Key parameters: `--center-hz` `--span-hz` `--rbw-hz` `--attenuation-db` `--points` `--operator` `--db`

## Testing

```bash
.venv/bin/python -m pytest
# hardware testing when true instrument is connected
.venv/bin/python -m pytest --visa=TCPIP0::192.168.10.2::hislip0::INSTR
```

