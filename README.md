# sCO2 System Control

Python control and logging scripts for a supercritical CO2 experimental system.

The repository contains standalone and integrated tools for RTD logging,
thermocouple logging, system-level monitoring, heater power logging, and
motorized stage automation.

## Repository Layout

```text
.
├── _RTD/              # Keithley DAQ6510 RTD logging and visualization
├── _TC/               # Thermocouple voltage logging and live plotting
├── _SYS/              # System monitor with pressure, flow, REFPROP, and P-h plot
└── _Heater with RTD/  # Integrated heater, RTD, power logger, and stage GUI
```

## Main Entry Points

- `_RTD/RTD_logger.py`: batch RTD resistance logging and Excel export.
- `_RTD/RTD_visualization.py`: real-time RTD temperature visualization.
- `_TC/TC_logger.py`: thermocouple voltage logging and temperature conversion.
- `_SYS/SYS_logger.py`: system monitor for temperature, pressure, mass flow,
  Reynolds number, and CO2 P-h diagram.
- `_Heater with RTD/final_automated.py`: integrated GUI automation sequence.

## Hardware and Runtime Requirements

The scripts are intended for a Windows laboratory PC with the relevant
instrument drivers and hardware connected.

Expected Python packages:

```text
pyvisa
numpy
pandas
matplotlib
customtkinter
ctREFPROP
openpyxl
```

External runtime dependencies are not committed to this repository:

- VISA runtime and `visa64.dll`
- REFPROP installation, `refprop64.dll`, and fluid files
- Vendor motor-control DLL such as `EMotionUniDevice.dll`
- Local calibration Excel workbooks

Place required DLLs, REFPROP files, and calibration workbooks in the same
locations expected by each script, or update the configuration dictionaries in
the scripts before running.

## Data Output

Scripts write measurement outputs under `data/` directories. Generated data,
plots, Python caches, IDE metadata, binary DLLs, REFPROP fluid files, and Excel
workbooks are intentionally ignored by Git.

## Safety Notes

These scripts directly control laboratory instruments including heater power
supplies and motorized stages. Before running an experiment:

- Verify VISA resource names and serial ports.
- Confirm current limits, target voltage, and stage positions.
- Check emergency stop behavior.
- Run low-risk dry tests with safe voltage and motion limits.
- Keep required vendor software and instrument drivers installed.

## Current Status

This repository is an initial preservation and cleanup point for the existing
experimental scripts. Further work should focus on configuration separation,
thread-safe GUI updates, safer error propagation, and hardware-independent
tests for parsing and unit conversion logic.
