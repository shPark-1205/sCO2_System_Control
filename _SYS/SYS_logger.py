import os
import time
import datetime
import pyvisa
import numpy as np
import pandas as pd
import math
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.lines import Line2D

# Attempt to import the REFPROP library for thermodynamic property calculations.
# If it's not found, a warning is printed, and calculations will be skipped.
try:
    from ctREFPROP.ctREFPROP import REFPROPFunctionLibrary
except ImportError:
    print("Warning: ctREFPROP library not found. Thermodynamic calculations will fail.")
    REFPROPFunctionLibrary = None

# =================================================================================
# ⚙️ Experiment Configuration
# =================================================================================
# This dictionary holds all the settings for the experiment, instruments, and plots.
# Modifying these values allows for easy reconfiguration without changing the main code logic.
CONFIG = {
    # --- Experiment Info ---
    "exp_objective": "20250918",
    "exp_sub_objective": "code_test",

    # --- Instrument & Channel Settings ---
    "agilent_visa_address": "USB0::0x2A8D::0x5101::MY58037062::0::INSTR",
    "pressure_transducer_com_ports": [f"ASRL20{i}" for i in range(1, 9)],  # ASRL201 to ASRL208
    "agilent_rtd_channels": "201, 202, 203, 204, 205, 208, 210",  # Channels for Resistance Temperature Detectors
    "agilent_dc_channels": "206, 207, 209, 216, 217",  # Channels for DC Voltage measurements

    # --- Measurement Settings ---
    "nplc": 2,  # Number of Power Line Cycles (integration time for measurements)
    "trigger_interval_sec": 4,  # NPLC 2: 4s, NPLC 10: 7s. Time between measurement triggers.

    # --- Plot Settings & Targets ---
    "target_pressure_mpa": 7.771,  # Target pressure in Megapascals for the P-h diagram
    "target_temp_c": 31.3,  # Target temperature in Celsius for the P-h diagram
    "plot_time_max_min": 120,  # Maximum time displayed on the x-axis of time-series plots (in minutes)
    "plot_inlet_temp_min": 10,  # Y-axis minimum for the inlet temperature plot
    "plot_inlet_temp_max": 30,  # Y-axis maximum for the inlet temperature plot
    "plot_inlet_pressure_min": 0,  # Y-axis minimum for the inlet pressure plot
    "plot_inlet_pressure_max": 1,  # Y-axis maximum for the inlet pressure plot
    "plot_reynolds_min": 0,  # Y-axis minimum for the Reynolds number plot
    "plot_reynolds_max": 100000,  # Y-axis maximum for the Reynolds number plot
    "plot_ph_enthalpy_min": 220,  # X-axis minimum for the P-h diagram (enthalpy)
    "plot_ph_enthalpy_max": 400,  # X-axis maximum for the P-h diagram (enthalpy)
    "plot_ph_pressure_min": 1,  # Y-axis minimum for the P-h diagram (pressure)
    "plot_ph_pressure_max": 7,  # Y-axis maximum for the P-h diagram (pressure)
}


# =================================================================================

class SystemMonitor:
    """
    Manages the entire process of data acquisition, processing, visualization,
    and logging for the experiment.
    """

    def __init__(self, config):
        """
        Initializes the SystemMonitor instance.
        Args:
            config (dict): A dictionary containing all configuration parameters.
        """
        self.config = config
        self.run_count = 0
        self.is_running = True

        # Placeholders for thermodynamic data calculated by REFPROP
        self.ph_data = {}
        self.isotherm_data = None
        self.target_h = None  # Enthalpy at the target T and P

        # Initialize the REFPROP library if available
        if REFPROPFunctionLibrary:
            self.RP = REFPROPFunctionLibrary(os.getcwd())
            self.RP.SETFLUIDSdll('CO2')  # Set the working fluid to CO2
        else:
            self.RP = None

        # --- Initialize data storage ---
        # A dictionary to hold all time-series data collected during the run.
        self.data = {
            "timestamps": [], "elapsed_seconds": [], "reynolds": [],
            "inlet": {"T": [], "P": []}, "chamber": {"T": [], "P": []},
            "loop_out": {"T": [], "P": []}, "chiller_out": {"T": [], "P": []},
            "pump_in": {"T": [], "P": []}, "pump_out": {"T": [], "P": []},
            "hx_out": {"T": [], "P": []}, "loop_in": {"T": [], "P": []},
            "heater_temp": [], "mass_flow": [], "mass_flow_temp": [], "env_temp": [],
            "properties": {"points": [{} for _ in range(8)], "errors": ["NA"] * 8},
            "raw": {"agilent": []}  # To store raw sensor values (V, Ω) for Excel export
        }

    def run(self):
        """Executes the entire monitoring and visualization process."""
        try:
            # Prepare the environment and hardware
            self._setup_directories()
            self._connect_instruments()
            self._configure_agilent()
            self._setup_plots()

            # --- Start Measurement and Animation ---
            self.agilent.write("INIT")  # Put Agilent DAQ in a wait-for-trigger state
            time.sleep(1)
            self._trigger_all_instruments()  # Send the first trigger to get initial data
            time.sleep(self.config['trigger_interval_sec'])

            print("\n🚀 Starting real-time measurement. Close the plot window to end.")

            # Create the animation that calls _update_plots at a regular interval
            self.ani = FuncAnimation(
                fig=self.fig, func=self._update_plots,
                interval=self.config['trigger_interval_sec'] * 1000,  # Interval in milliseconds
                cache_frame_data=False  # Recommended for real-time data to prevent memory issues
            )
            plt.show()  # Display the plot window

        except Exception as e:
            print(f"\n❌ An unexpected error occurred: {e}")
        finally:
            # Ensure data is saved and instruments are closed safely on exit
            self._save_data_to_excel()
            self._close_instruments()

    def _setup_directories(self):
        """Creates the necessary directories for saving data and plots."""
        self.time_title = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        base_path = f"data/_SYS-{self.config['exp_objective']}"
        self.sub_path = f"{base_path}/_SYS-{self.config['exp_sub_objective']}"
        self.output_path = f"{self.sub_path}/SYS-{self.time_title}"
        os.makedirs(f"{self.output_path}/ph", exist_ok=True)  # Directory for saving plot images
        print(f"✅ Data storage path created: {self.output_path}")

    def _connect_instruments(self):
        """Establishes connection with all measurement instruments via pyvisa."""
        print("🔄 Connecting to instruments...")
        self.rm = pyvisa.ResourceManager()

        # Connect to Agilent DAQ
        self.agilent = self.rm.open_resource(self.config['agilent_visa_address'])
        self.agilent.write_termination = "\n"
        self.agilent.timeout = 30000  # 30-second timeout
        print(f"  - Agilent DAQ: {self.agilent.query('*IDN?').strip()}")

        # Connect to Pressure Transducers (DPTs)
        self.dpts = []
        for port in self.config['pressure_transducer_com_ports']:
            dpt = self.rm.open_resource(f"{port}::INSTR")
            dpt.write_termination = '\r'
            dpt.timeout = 30000
            self.dpts.append(dpt)
            print(f"  - DPT on {port}: {dpt.query('#*ID?').strip()}")

        print("✅ All instruments connected.")
        self.agilent.write("*CLS")  # Clear status registers

    def _configure_agilent(self):
        """Sends a series of SCPI commands to configure the Agilent DAQ."""
        rtd = self.config['agilent_rtd_channels']
        dc = self.config['agilent_dc_channels']
        nplc = self.config['nplc']

        commands = [
            "*RST", "*CLS", "DISP ON",
            f"CONF:FRES DEF,(@{rtd})", f"SENS:FRES:RANG 1000,(@{rtd})",  # Configure 4-wire resistance
            f"SENS:FRES:NPLC {nplc},(@{rtd})", f"SENS:FRES:OCOM ON,(@{rtd})",  # Set NPLC and offset compensation
            f"CONF:VOLT:DC DEF,(@{dc})", f"SENS:VOLT:DC:RANG 10,(@{dc})",  # Configure DC voltage
            f"SENS:VOLT:DC:NPLC {nplc},(@{dc})",
            f"ROUT:SCAN (@{rtd},{dc})",  # Define the scan list
            "TRIG:SOUR BUS", "TRIG:COUN INF", "FORM:READ:CHAN ON",  # Set trigger source and format
            "FORM:READ:TIME ON", "FORM:READ:TIME:TYPE REL"
        ]

        print("⚙️  Applying Agilent DAQ settings...")
        for cmd in commands:
            self.agilent.write(cmd)
            time.sleep(0.05)  # Small delay to ensure commands are processed
        print("✅ Agilent DAQ configuration complete.")

    def _update_plots(self, frame):
        """
        This is the core function for the animation. It's called at each interval.
        It fetches new data and updates all the plot lines and text.
        """
        self._update_data()  # Get the latest data from instruments

        if not self.data['timestamps']: return []  # Skip if no data is available yet

        # --- Update Left Plots (Time-Series) ---
        elapsed_min = np.array(self.data['elapsed_seconds']) / 60
        self.line1.set_data(elapsed_min, self.data['inlet']['T'])
        self.line2.set_data(elapsed_min, self.data['inlet']['P'])
        self.line3.set_data(elapsed_min, self.data['reynolds'])

        # Dynamically adjust plot axes to fit the data
        if len(elapsed_min) > 1:
            self.ax1.set_xlim(0, max(elapsed_min) * 1.1)
            if self.data['inlet']['T']:
                self.ax1.set_ylim(min(self.data['inlet']['T']) - 2, max(self.data['inlet']['T']) + 2)
            if self.data['inlet']['P']:
                self.ax2.set_ylim(min(self.data['inlet']['P']) * 0.9, max(self.data['inlet']['P']) * 1.1)
            if self.data['reynolds']:
                self.ax3.set_ylim(min(self.data['reynolds']) * 0.9, max(self.data['reynolds']) * 1.1)

        # Update text labels with the latest values
        self.ax1_text.set_text(f"T_inlet = {self.data['inlet']['T'][-1]:.2f}°C")
        self.ax2_text.set_text(f"P_inlet = {self.data['inlet']['P'][-1]:.3f} MPa")
        self.ax3_text_re.set_text(f"Re = {self.data['reynolds'][-1]:>5.0f}")
        self.ax3_text_flow.set_text(f"m_dot = {self.data['mass_flow'][-1]:>4.0f} g/min")

        # --- Update Right Plot (P-h Diagram) ---
        props = self.data['properties']['points']
        h = [p.get('h', 0) for p in props]  # Enthalpy
        P = [p.get('P', 0) for p in props]  # Pressure

        # Define the points for the thermodynamic cycle line
        ph_cycle_h = [h[5], h[7], h[3], h[1], h[0], h[4], h[6], h[5]]
        ph_cycle_p = [P[5], P[7], P[3], P[1], P[0], P[4], P[6], P[5]]

        self.line4_cycle.set_data(ph_cycle_h, ph_cycle_p)

        # Update the position of each marker on the P-h diagram
        self.line4_pumpI.set_data([h[5]], [P[5]])
        self.line4_pumpO.set_data([h[7]], [P[7]])
        self.line4_hxo.set_data([h[3]], [P[3]])
        self.line4_in.set_data([h[1]], [P[1]])
        self.line4_chm.set_data([h[0]], [P[0]])
        self.line4_out.set_data([h[4]], [P[4]])
        self.line4_cho.set_data([h[6]], [P[6]])

        # Dynamically update axis limits of P-h diagram
        if all(ph_cycle_p) and all(ph_cycle_h):
            min_h, max_h = min(ph_cycle_h), max(ph_cycle_h)
            self.ax4.set_xlim(min_h-50, max_h+50)
            min_p, max_p = min(ph_cycle_p), max(ph_cycle_p)
            self.ax4.set_ylim(min_p-0.5, max_p+0.5)

        # Update text labels for phase states
        errors = self.data['properties']['errors']
        self.ax4_text_in.set_text(
            f"Nozzle : {errors[1] if len(errors) > 1 else ''} (ρ={self.data['properties']['points'][1].get('rho', 0):.0f})")
        self.ax4_text_chm.set_text(f"Chamber: {errors[0] if len(errors) > 0 else ''}")

        # Save a snapshot of the current plot
        plt.savefig(f"{self.output_path}/ph/_SYS-{self.config['exp_sub_objective']}-{self.time_title}.png")

        # Trigger the next measurement cycle
        self._trigger_all_instruments()

        # Return the list of updated plot elements for efficient animation rendering
        return [self.line1, self.line2, self.line3, self.line4_cycle, self.line4_pumpI,
                self.line4_pumpO, self.line4_hxo, self.line4_in, self.line4_chm,
                self.line4_out, self.line4_cho]

    def _update_data(self):
        """Fetches, processes, and stores data from all instruments."""
        self.run_count += 1

        # --- 1. Fetch Raw Data ---
        raw_agilent_data = self.agilent.query("R?")
        raw_pressure_data = np.array([float(dpt.read()[1:11]) for dpt in self.dpts])

        # --- 2. Pre-process and Convert Data ---
        processed_agilent_data = self._process_agilent_string(raw_agilent_data)
        converted_agilent_data = self._convert_agilent_units(processed_agilent_data)
        calibrated_pressures = self._calibrate_pressures(raw_pressure_data)

        # Store raw agilent values (before unit conversion) for the Excel export
        self.data['raw']['agilent'].append(processed_agilent_data)

        # --- 3. Calculate Thermodynamic Properties ---
        if self.RP:
            self._calculate_all_properties(converted_agilent_data, calibrated_pressures)

        # --- 4. Store Processed Data ---
        self._store_processed_data(converted_agilent_data, calibrated_pressures)

        # --- 5. Log to Console ---
        self._log_to_console()

    def _process_agilent_string(self, raw_data):
        """Parses the raw string from the Agilent DAQ and averages multi-sample data."""
        items = raw_data.strip().split(',')

        # Clean up the first data point which may contain header info
        first_item = items[0]
        start_pos = -1
        plus_pos = first_item.find('+')
        minus_pos = first_item.find('-')
        if plus_pos != -1: start_pos = plus_pos
        if minus_pos != -1: start_pos = minus_pos if start_pos == -1 else min(start_pos, minus_pos)
        if start_pos != -1: items[0] = first_item[start_pos:]

        # Reshape data into [value, time, channel] and get unique channels
        data_np = np.array(items, dtype=np.float64).reshape(-1, 3)
        unique_channels = np.unique(data_np[:, 2])

        # Average readings for each channel (in case of multiple samples per trigger)
        processed = np.array([])
        for ch in unique_channels:
            mean_val = np.mean(data_np[data_np[:, 2] == ch, 0])
            processed = np.append(processed, mean_val)
        return processed

    def _convert_agilent_units(self, data):
        """Converts raw sensor readings (V, Ω) to physical units (°C, g/min)."""
        converted = np.array([])
        for i, val in enumerate(data):
            if i in [5, 6, 8]:  # Thermocouples (DC Voltage to Temperature)
                temp = 175.0 * val - 375.0
                if i == 5: temp -= 0.35  # TC201 (mapped to sorted channel 206)
                if i == 6: temp -= 0.3  # TC209 (mapped to 207)
                if i == 8: temp += 1.26  # TC206 Jet (mapped to 209)
                converted = np.append(converted, temp)
            elif i == 10:  # Mass flow meter (DC Voltage to g/min)
                flow = 1000.0 * val - 1000.0
                converted = np.append(converted, flow)
            elif i == 11:  # Mass flow meter temperature sensor (DC Voltage to °C)
                temp = 15.0 * val - 5.0
                converted = np.append(converted, temp)
            else:  # RTDs (Resistance to Temperature using Callendar-Van Dusen equation)
                R0, a1, a2 = 100.0, 0.0039083, -0.0000005775
                a0 = 1.0 - (val / R0)
                # Handle potential math domain error for invalid resistance values
                discriminant = a1 ** 2 - 4 * a2 * a0
                temp = (-a1 + math.sqrt(discriminant)) / (
                            2.0 * a2) if discriminant >= 0 else -999  # Return -999 on error
                converted = np.append(converted, temp)
        return converted

    def _calibrate_pressures(self, raw_pressures):
        """Applies calibration offsets to the raw pressure readings."""
        calibrated = np.zeros(8)
        # Offsets correspond to the DPT input order: JJ-1006, 1007, 1005, 1004, 1008, 1002, 1001, 1003
        offsets = [0.00992, 0.0128, 0.00862, 0.00234, 0.00262, 0.00704, 0.00096, 0.00716]

        # Apply offset only if the pressure is within the calibrated range (7-8 MPa)
        for i, p in enumerate(raw_pressures):
            offset = offsets[i]
            calibrated[i] = p + offset if 7.0 <= p <= 8.0 else p
        return calibrated

    def _calculate_all_properties(self, temps, pressures):
        """Calculates thermodynamic properties for all measurement points using REFPROP."""
        # Map of (Temperature, Pressure) for each of the 8 main points in the system
        point_map = [(temps[0], pressures[0]), (temps[1], pressures[1]), (temps[2], pressures[2]),
                     (temps[3], pressures[3]), (temps[4], pressures[4]), (temps[5], pressures[5]),
                     (temps[6], pressures[6]), (temps[7], pressures[7])]

        all_errors = []
        for i, (t, p) in enumerate(point_map):
            props = self._get_refprop(t, p)
            # Store the calculated properties
            self.data['properties']['points'][i] = {
                'T': t, 'P': p,
                'h': props.get('h', 0), 's': props.get('s', 0),
                'rho': props.get('D', 0), 'vis': props.get('vis', 1e-5)
            }
            all_errors.append(props.get('phase_str', 'E'))  # Store phase string (L, G, S, or E for error)
        self.data['properties']['errors'] = all_errors

    def _get_refprop(self, temp_c, pressure_mpa):
        """A helper function to call REFPROP for a single point."""
        if not self.RP: return {}
        T_k, P_pa = temp_c + 273.15, pressure_mpa * 1e6  # Convert to SI units

        phase_map = {"Subcooled liquid": "L", "Superheated gas": "G", "Supercritical": "S"}
        phase = self.RP.REFPROP1dll('TP', 'PHASE', 21, 1, T_k, P_pa, [])

        # Return a dictionary of calculated properties
        return {
            "h": self.RP.REFPROP1dll('TP', 'H', 21, 1, T_k, P_pa, []).c / 1000.0,  # Enthalpy (J/kg -> kJ/kg)
            "s": self.RP.REFPROP1dll('TP', 's', 21, 1, T_k, P_pa, []).c,  # Entropy (J/kg-K)
            "D": self.RP.REFPROP1dll('TP', 'D', 21, 1, T_k, P_pa, []).c,  # Density (kg/m^3)
            "vis": self.RP.REFPROP1dll('TP', 'VIS', 21, 1, T_k, P_pa, []).c,  # Viscosity (Pa-s)
            "phase_str": phase_map.get(phase.herr, "E")
        }

    def _store_processed_data(self, temps, pressures):
        """Appends the latest processed data to the main data dictionary."""
        now = datetime.datetime.now()
        # self.data['timestamps'].append(now.strftime('%Y-%m-%d %H:%M:%S'))
        self.data['timestamps'].append(now.strftime('%H:%M:%S'))
        self.data['elapsed_seconds'].append(self.config['trigger_interval_sec'] * (self.run_count - 1))

        # Map the 12-element data arrays to their named locations
        self.data['inlet']['T'].append(temps[1]);
        self.data['inlet']['P'].append(pressures[1])
        self.data['chamber']['T'].append(temps[0]);
        self.data['chamber']['P'].append(pressures[0])
        self.data['loop_out']['T'].append(temps[4]);
        self.data['loop_out']['P'].append(pressures[4])
        self.data['chiller_out']['T'].append(temps[6]);
        self.data['chiller_out']['P'].append(pressures[6])
        self.data['pump_in']['T'].append(temps[5]);
        self.data['pump_in']['P'].append(pressures[5])
        self.data['pump_out']['T'].append(temps[7]);
        self.data['pump_out']['P'].append(pressures[7])
        self.data['hx_out']['T'].append(temps[3]);
        self.data['hx_out']['P'].append(pressures[3])
        self.data['loop_in']['T'].append(temps[2]);
        self.data['loop_in']['P'].append(pressures[2])

        self.data['heater_temp'].append(temps[8])
        self.data['env_temp'].append(temps[9])
        self.data['mass_flow'].append(max(0, temps[10]))  # Mass flow cannot be negative
        self.data['mass_flow_temp'].append(temps[11])

        # Calculate Reynolds number
        mass_flow_si = self.data['mass_flow'][-1] / 1000.0 / 60.0  # g/min to kg/s
        viscosity = self.data['properties']['points'][1].get('vis', 1e-5)
        reynolds = 4.0 * mass_flow_si / (viscosity * math.pi * 0.0094) if viscosity > 0 else 0
        self.data['reynolds'].append(reynolds)

    def _log_to_console(self):
        """Logs the latest data to the console in a formatted table."""
        # Print a header every 10 data points for readability
        if self.run_count % 10 == 1:
            self._print_log_header()

        d = self.data;
        p = d['properties']['points']
        errs = (d['properties']['errors'] + ["NA"] * 8)[:8]

        # Print the formatted data string
        print(
            f"{self.run_count:0>4}\t "
            # f"{d['timestamps'][-1].split(' ')[1]}\t "
            f"{d['timestamps'][-1]}\t "
            f"{d['reynolds'][-1]:>5.0f}\t "
            f"{d['inlet']['T'][-1]:.3f}\t "
            f"{d['chamber']['T'][-1]:.3f}\t "
            f"{d['inlet']['P'][-1]:.4f}\t "
            f"{d['chamber']['P'][-1]:.4f}\t "
            f"{errs[1]} {errs[0]} {errs[4]}\t "
            f"{d['loop_out']['T'][-1]:.2f}\t "
            f"{d['chiller_out']['T'][-1]:.2f}\t "
            f"{d['pump_in']['T'][-1]:.2f}\t "
            f"{d['pump_out']['T'][-1]:.2f}\t "
            f"{d['hx_out']['T'][-1]:.2f}\t "
            f"{d['loop_in']['T'][-1]:.2f}\t "
            f"{d['mass_flow_temp'][-1]:.2f}\t "
            f"{d['heater_temp'][-1]:.2f}\t "
            f"{d['env_temp'][-1]:.2f}\t "
            f"{d['loop_out']['P'][-1]:.4f}\t "
            f"{d['chiller_out']['P'][-1]:.4f}\t "
            f"{d['pump_in']['P'][-1]:.4f}\t "
            f"{d['pump_out']['P'][-1]:.4f}\t "
            f"{d['hx_out']['P'][-1]:.4f}\t "
            f"{d['loop_in']['P'][-1]:.4f}\t "
            f"{d['mass_flow'][-1]:>4.1f}\t "
            f"{errs[5]} ({p[5].get('rho', 0):.0f})"
        )

    def _print_log_header(self):
        """Prints the complex column header for the console log."""
        print(
            "\ncnt \tTime    \tRe   \tT_in\tT_chm\tP_in  \tP_chm \tphs\t"
            "\tT_LpO\tT_ChO\tT_PmI\tT_PmO\tT_HXO\tT_LPI\tT_MfT\tT_Ctr\tT_Env\t"
            "P_LpO  \tP_ChO  \tP_PmI  \tP_PmO  \tP_HXO  \tP_LPI  \tm   \tppSt"
        )

    def _trigger_all_instruments(self):
        """Sends a software trigger command to all instruments to start a new measurement."""
        self.agilent.write("*TRG")
        for dpt in self.dpts:
            dpt.write("#*?")

    def _save_data_to_excel(self):
        """Saves all collected data to an Excel file in a specific format."""
        if not self.data['timestamps']:
            print("\nℹ️  No data to save.")
            return

        # Convert list of raw Agilent data arrays into a 2D numpy array for easy slicing
        raw_agilent_data = np.array(self.data['raw']['agilent'])

        # Create individual pandas DataFrames for each block of data
        df_time = pd.DataFrame({'Time': self.data['timestamps']})
        df_count = pd.DataFrame({'Run': range(1, len(self.data['timestamps']) + 1)})
        df_sec = pd.DataFrame({'sec': self.data['elapsed_seconds']})
        df_mass = pd.DataFrame({'m [g/min]': self.data['mass_flow']})

        df_inlet = pd.DataFrame({'T_in[C]': self.data['inlet']['T'], 'P_in[MPa]': self.data['inlet']['P']})
        df_chamber = pd.DataFrame({'T_chm[C]': self.data['chamber']['T'], 'P_chm[MPa]': self.data['chamber']['P']})
        df_chiller_af = pd.DataFrame(
            {'T_chAf[C]': self.data['chiller_out']['T'], 'P_chAf[MPa]': self.data['chiller_out']['P']})
        df_roop_out = pd.DataFrame(
            {'T_rpOut[C]': self.data['loop_out']['T'], 'P_rpOut[MPa]': self.data['loop_out']['P']})
        df_pump_in = pd.DataFrame({'T_pmpIn[C]': self.data['pump_in']['T'], 'P_pmpIn[MPa]': self.data['pump_in']['P']})
        df_pump_out = pd.DataFrame(
            {'T_pmpOut[C]': self.data['pump_out']['T'], 'P_pmpOut[MPa]': self.data['pump_out']['P']})
        df_hx_af = pd.DataFrame({'T_hxAf[C]': self.data['hx_out']['T'], 'P_hxAf[MPa]': self.data['hx_out']['P']})
        df_roop_in = pd.DataFrame({'T_rpIn[C]': self.data['loop_in']['T'], 'P_rpIn[MPa]': self.data['loop_in']['P']})

        df_other = pd.DataFrame({'THXct[C]': self.data['heater_temp'], 'T_m[C]': self.data['mass_flow_temp'],
                                 'T_emv[C]': self.data['env_temp']})

        # Create DataFrame for raw sensor data, mapping each column to the correct index
        df_raw = pd.DataFrame({
            'T_in[Ω]': raw_agilent_data[:, 1], 'T_chm[Ω]': raw_agilent_data[:, 0],
            'T_rpOut[Ω]': raw_agilent_data[:, 4], 'T_chAf[V]': raw_agilent_data[:, 6],
            'T_pmpIn[V]': raw_agilent_data[:, 5], 'T_pmpOut[Ω]': raw_agilent_data[:, 7],
            'T_hxAf[Ω]': raw_agilent_data[:, 3], 'T_rpIn[Ω]': raw_agilent_data[:, 2],
            'T_crt[V]': raw_agilent_data[:, 8], 'T_env[Ω]': raw_agilent_data[:, 9],
            'T_m[V]': raw_agilent_data[:, 10], 'T_mT[V]': raw_agilent_data[:, 11]
        })

        # Concatenate all DataFrames into a single one in the specified order
        final_df = pd.concat([
            df_time, df_count, df_sec, df_mass, df_inlet, df_chamber, df_chiller_af,
            df_roop_out, df_pump_in, df_pump_out, df_hx_af, df_roop_in, df_other, df_raw
        ], axis=1)

        # Save to two locations: one in the specific run folder, one in the sub-objective folder
        filename_run = f"{self.output_path}/_SYS-{self.time_title}.xlsx"
        filename_sub = f"{self.sub_path}/_SYS-{self.config['exp_sub_objective']}-{self.time_title}.xlsx"

        final_df.to_excel(filename_run, index=False)
        final_df.to_excel(filename_sub, index=False)
        print(f"\n💾 Data saved successfully to {filename_run}")

    def _close_instruments(self):
        """Safely closes all instrument connections."""
        print("\n🧹 Performing cleanup and shutdown...")
        if hasattr(self, 'agilent'):
            try:
                self.agilent.write("ABOR")  # Abort any ongoing measurements
                self.agilent.close()
                for dpt in self.dpts:
                    dpt.close()
                self.rm.close()
                print("✅ All instruments closed safely.")
            except Exception as e:
                print(f"⚠️ Error during cleanup: {e}")
        print("👋 Program finished.")

    def _setup_plots(self):
        """
        Configures the Matplotlib figure and axes for all plots.
        This is called once at the beginning of the run.
        """
        self.fig = plt.figure(figsize=(20, 10))
        self.fig.canvas.manager.set_window_title('System Monitor')

        # Use GridSpec for more flexible subplot layout
        gs = self.fig.add_gridspec(3, 2, width_ratios=[1, 1.2])
        self.ax1 = self.fig.add_subplot(gs[0, 0])
        self.ax2 = self.fig.add_subplot(gs[1, 0], sharex=self.ax1)
        self.ax3 = self.fig.add_subplot(gs[2, 0], sharex=self.ax1)
        self.ax4 = self.fig.add_subplot(gs[:, 1])

        plt.subplots_adjust(top=0.96, bottom=0.1, left=0.06, right=0.98, hspace=0.3, wspace=0.2)
        plt.setp(self.ax1.get_xticklabels(), visible=False)  # Hide x-axis labels for top plots
        plt.setp(self.ax2.get_xticklabels(), visible=False)

        # Pre-calculate background lines for the P-h diagram
        self._phdiagram_setting()

        # --- Configure Left Plots ---
        self.ax1.set_ylabel('T_nozzle [°C]');
        self.ax1.grid(True, linestyle=':')
        self.ax1.set_ylim(self.config['plot_inlet_temp_min'], self.config['plot_inlet_temp_max'])
        self.line1, = self.ax1.plot([], [], 'r-', lw=2)

        self.ax2.set_ylabel('P_nozzle [MPa]');
        self.ax2.grid(True, linestyle=':')
        self.ax2.set_ylim(self.config['plot_inlet_pressure_min'], self.config['plot_inlet_pressure_max'])
        self.line2, = self.ax2.plot([], [], 'b-', lw=2)

        self.ax3.set_xlabel('Time [min]');
        self.ax3.grid(True, linestyle=':')
        self.ax3.set_ylabel('Reynolds Number')
        self.ax3.set_ylim(self.config['plot_reynolds_min'], self.config['plot_reynolds_max'])
        self.ax3.set_xlim(0, self.config['plot_time_max_min'])
        self.line3, = self.ax3.plot([], [], 'g-', lw=2)

        # Create text objects for displaying live data on plots
        self.ax1_text = self.ax1.text(0.02, 0.85, "", transform=self.ax1.transAxes, fontsize=12)
        self.ax2_text = self.ax2.text(0.02, 0.85, "", transform=self.ax2.transAxes, fontsize=12)
        self.ax3_text_re = self.ax3.text(0.02, 0.85, "", transform=self.ax3.transAxes, fontsize=12)
        self.ax3_text_flow = self.ax3.text(0.02, 0.65, "", transform=self.ax3.transAxes, fontsize=12)

        # --- Configure Right P-h diagram ---
        self.ax4.set_xlabel('Enthalpy [kJ/kg]')
        self.ax4.set_ylabel('Pressure [MPa]')
        self.ax4.set_xlim(self.config['plot_ph_enthalpy_min'], self.config['plot_ph_enthalpy_max'])
        self.ax4.set_ylim(self.config['plot_ph_pressure_min'], self.config['plot_ph_pressure_max'])
        self.ax4.grid(True, linestyle='--')

        # Plot the pre-calculated saturation and isotherm lines
        if self.ph_data:
            self.ax4.plot(self.ph_data['liquid_h'], self.ph_data['liquid_p'], 'k-', lw=2)
            self.ax4.plot(self.ph_data['gas_h'], self.ph_data['gas_p'], 'k-', lw=2)
            self.ax4.plot(self.ph_data['scf_liquid_h'], self.ph_data['scf_liquid_p'], 'C1-', lw=2)
            self.ax4.plot(self.ph_data['scf_gas_h'], self.ph_data['scf_gas_p'], 'C1-', lw=2)

        if self.isotherm_data is not None:
            for i in range(0, len(self.isotherm_data), 2):
                h_vals, p_vals = self.isotherm_data[i], self.isotherm_data[i + 1]
                self.ax4.plot(h_vals, p_vals, 'r-', lw=0.5, alpha=0.3)

        # Plot the target condition marker and guide lines
        target_p = self.config['target_pressure_mpa']
        target_h = self.target_h
        if target_h:
            self.ax4.plot(target_h, target_p, '*', c='deeppink', markersize=15, mec='k', label='Target', zorder=10)
            self.ax4.axhline(y=target_p, color='g', linestyle='-.', alpha=0.5)
            self.ax4.axvline(x=target_h, color='g', linestyle='-.', alpha=0.5)

        # Create text objects for phase state information
        self.ax4_text_in = self.ax4.text(0.05, 0.15, "", transform=self.ax4.transAxes, fontsize=10)
        self.ax4_text_chm = self.ax4.text(0.05, 0.10, "", transform=self.ax4.transAxes, fontsize=10)

        # Create line/marker objects for the live data points on the P-h diagram
        self.line4_cycle, = self.ax4.plot([], [], 'k--', lw=0.75, label='_nolegend_')
        self.line4_pumpI, = self.ax4.plot([], [], 'p', c='blue', mec='k', markersize=8, label='Pump In')
        self.line4_pumpO, = self.ax4.plot([], [], 's', c='darkcyan', mec='k', markersize=8, label='Pump Out')
        self.line4_hxo, = self.ax4.plot([], [], '.', c='red', mec='k', markersize=12, label='HX Out')
        self.line4_in, = self.ax4.plot([], [], '+', c='orange', mec='k', markersize=10, mew=2, label='Nozzle In')
        self.line4_chm, = self.ax4.plot([], [], 'o', c='gold', mec='k', markersize=8, label='Chamber')
        self.line4_out, = self.ax4.plot([], [], 'X', c='m', mec='k', markersize=8, label='Loop Out')
        self.line4_cho, = self.ax4.plot([], [], 'v', c='black', mec='k', markersize=8, label='Chiller Out')
        self.ax4.legend(loc='upper right')

    def _phdiagram_setting(self):
        """Calculates the background lines (saturation, isotherms) for the P-h diagram."""
        if not self.RP:
            print("Warning: REFPROP not available, skipping P-h diagram background.")
            return

        # Get critical temperature and pressure for CO2
        TC = self.RP.REFPROP1dll("PT", "TC", 21, 0, 0, 0, [1]).c - 273.15
        PC = self.RP.REFPROP1dll("PT", "PC", 21, 0, 0, 0, [1]).c / 1e6

        # Calculate saturated liquid and vapor lines
        p_liq, h_liq = self._cal_h("Q", 0, 5.5, PC, 1001)  # Q=0 for saturated liquid
        p_gas, h_gas = self._cal_h("Q", 1, 5.5, PC, 1001)  # Q=1 for saturated vapor
        self.ph_data.update({'liquid_p': p_liq, 'liquid_h': h_liq, 'gas_p': p_gas, 'gas_h': h_gas})

        # Calculate lines in the supercritical fluid region
        h_val_at_pc = self.RP.REFPROP1dll('PQ', 'h', 21, 1, PC * 1e6, 0, []).c / 1000.0
        self.ph_data['scf_gas_p'] = np.array([PC, PC])
        self.ph_data['scf_gas_h'] = np.array([h_val_at_pc, 600])
        p_scf_liq, h_scf_liq = self._cal_h("T", TC, PC, 13, 1001)
        self.ph_data.update({'scf_liquid_p': p_scf_liq, 'scf_liquid_h': h_scf_liq})

        # Calculate isotherm lines
        isotherms = np.empty((0, 1001))
        for temp in np.arange(0, 60, 1):  # from 0 to 59°C in 1°C steps
            p, h = self._cal_h("T", temp, 5, 13, 1001)
            isotherms = np.append(isotherms, [h, p], axis=0)
        self.isotherm_data = isotherms

        # Calculate and store the specific enthalpy for the target T and P
        target_props = self._get_refprop(self.config['target_temp_c'], self.config['target_pressure_mpa'])
        self.target_h = target_props.get('h', 0)

    def _cal_h(self, fix_prop, fix_value, p_start, p_end, divider):
        """
        Helper function to calculate enthalpy over a pressure range for a fixed property (e.g., T, Q, or S).

        Args:
            fix_prop (str): The property to hold constant ('T' for temperature, 'Q' for quality).
            fix_value (float): The value of the fixed property.
            p_start (float): Starting pressure in MPa.
            p_end (float): Ending pressure in MPa.
            divider (int): The number of points to calculate.

        Returns:
            tuple: (pressure_range_in_MPa, enthalpy_values_in_kJ/kg)
        """
        p_range = np.linspace(p_start * 1e6, p_end * 1e6, divider)  # Pressure in Pa
        h_values = []

        if fix_prop == "T": fix_value += 273.15  # Convert to Kelvin if fixing temperature

        for p_run in p_range:
            # Call REFPROP to get enthalpy (H) given pressure (P) and the fixed property
            h = self.RP.REFPROP1dll(f"P{fix_prop}", 'H', 21, 1, p_run, fix_value, []).c / 1000.0  # J/kg to kJ/kg
            h_values.append(h)

        return p_range / 1e6, np.array(h_values)


if __name__ == "__main__":
    # Create an instance of the SystemMonitor and run it.
    monitor = SystemMonitor(CONFIG)
    monitor.run()
