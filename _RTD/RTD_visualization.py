import sys
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
from matplotlib.ticker import ScalarFormatter

# =================================================================================
# ⚙️ Experiment Configuration
# =================================================================================
CONFIG = {
    # --- Experiment Info ---
    "exp_objective": "20260116",
    "exp_sub_objective": "jet_heateron_steady-test-new_correction",

    # --- Instrument & Channel Settings ---
    "visa_resource_name": "USB0::0x05E6::0x6510::04444312::0::INSTR",
    # "rtd_channels_str": "103, 104, 113, 106, 107, 114, 109, 110, 111, 112",
    "rtd_channels_str": "104, 113, 106, 107, 114, 109",

    # --- Measurement Settings ---
    "nplc": 2.0,
    "trigger_interval_sec": 2,

    # --- Plot Settings ---
    "plot_temp_min": 20,
    "plot_temp_max": 24,
    "plot_time_max_min": 5,
    "rtd_first_coord": -2.25,  # First point coordinate of RTD for r/d calculation
    "plot_rd_min": -1.0,
    "plot_rd_max": 1.0,

    # --- Normalization ---
    "calibration_file_path": './A_calibration_curve_RTD_poly_final_Quadratic_2to7RTD_new3.xlsx',
    "normalization_enabled": True,
    "normalization_row_index": 7,  # Row number of EXCEL FILE
}


# =================================================================================

class RealTimeDAQLogger:
    """
    Logs and visualizes RTD data from a Keithley DAQ6510 in real-time,
    displaying both time-series and spatial-profile plots.
    """

    def __init__(self, config):
        self.config = config
        self.channels = [int(ch) for ch in self.config['rtd_channels_str'].split(', ')]

        # --- Data Storage using Dictionaries ---
        self.run_count = 0
        self.timestamps = []
        self.elapsed_seconds = []
        self.temperatures = {ch: [] for ch in self.channels}
        self.resistances = {ch: [] for ch in self.channels}
        self.buffer_read_count = np.array([0])  # For tracking DAQ buffer reads

    def run(self):
        """Executes the entire logging and visualization process."""
        try:
            self._setup_directories()
            self._load_calibration_data()
            self._connect_dmm()
            self._configure_dmm()
            self._setup_plots()

            # --- Start Measurement and Animation ---
            self.dmm.write("INIT")
            time.sleep(0.1)
            self.dmm.write('*TRG')
            time.sleep(self.config['trigger_interval_sec'])

            print("\n🚀 Starting real-time measurement. Close the plot window to end.")
            self._print_log_header()

            self.ani = FuncAnimation(
                fig=self.fig, func=self._update_plots,
                interval=self.config['trigger_interval_sec'] * 1000,
                cache_frame_data=False
            )
            plt.show()

        except Exception as e:
            print(f"\n❌ An unexpected error occurred: {e}")
        finally:
            self._save_data_to_excel()
            self._close_dmm()

    def _setup_directories(self):
        self.time_title = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        self.path_title = f"data/_RTD-{self.config['exp_objective']}"
        self.path_sub_title = f"{self.path_title}/_vRTD-{self.config['exp_sub_objective']}"
        self.output_path = f"{self.path_sub_title}/_vRTD-{self.config['exp_sub_objective']}_{self.time_title}"
        try:
            os.makedirs(f"{self.output_path}/pic", exist_ok=True)
            print(f"✅ Data storage path created: {self.output_path}")
        except OSError:
            print("Error: Creating directory")

    def _load_calibration_data(self):
        try:
            fit_df = pd.read_excel(self.config['calibration_file_path'])
            self.calibration_coeffs = fit_df.values

        except FileNotFoundError:
            print(f"❌ ERROR: Calibration file not found at '{self.config['calibration_file_path']}'")
            sys.exit(1)
        except Exception as e:
            print(f"❌ ERROR: Could not process calibration file. Check its format. Error: {e}")
            sys.exit(1)

    def _connect_dmm(self):
        try:
            print("🔄 Connecting to DAQ...")
            self.rm = pyvisa.ResourceManager()
            self.dmm = self.rm.open_resource(self.config['visa_resource_name'])
            self.dmm.write_termination = "\n"
            self.dmm.timeout = 30000
            self.dmm.write("ABOR")
            print(f"✅ Device connected: {self.dmm.query('*IDN?').strip()}")
            self.dmm.write("SYST:CLE")
        except pyvisa.errors.VisaIOError as e:
            print(f"❌ DAQ connection failed.\n   Error: {e}")
            sys.exit(1)

    def _configure_dmm(self):
        channels_str = ",".join(map(str, self.channels))
        settings = [
            "*RST", "DISP:SCR HOME", "TRAC:CLE 'defbuffer1'",
            f"SENS:FUNC 'FRES',(@{channels_str})",
            f"SENS:FRES:RANG 1000,(@{channels_str})",
            f"SENS:FRES:AZER ON,(@{channels_str})",
            f"SENS:FRES:NPLC {self.config['nplc']},(@{channels_str})",
            f"SENS:FRES:OCOM ON,(@{channels_str})",
            "ROUT:SCAN:COUNT:SCAN 0",
            f"ROUT:SCAN:CRE (@{channels_str})",
            "ROUT:SCAN:BUFF 'defbuffer1'",
        ]
        print("⚙️  Applying DAQ settings...")
        for cmd in settings:
            self.dmm.write(cmd)
            time.sleep(0.05)
        print("✅ DAQ configuration complete.")

    def _setup_plots(self):
        self.fig, (self.ax_temp_time, self.ax_temp_rd) = plt.subplots(2, 1, figsize=(10, 8))
        self.fig.canvas.manager.set_window_title('Real-time RTD Monitor')
        plt.subplots_adjust(hspace=0.3)

        self.plain_formatter = ScalarFormatter(useOffset=False)
        self.plain_formatter.set_scientific(False)

        # --- Top Plot: Temperature vs. Time ---
        self.ax_temp_time.set_ylabel('Temperature [°C]')
        self.ax_temp_time.set_xlim(0, self.config['plot_time_max_min'])
        # self.ax_temp_time.set_ylim(self.config['plot_temp_min'], self.config['plot_temp_max'])
        self.ax_temp_time.grid(True, linestyle='--')
        self.ax_temp_time.yaxis.set_major_formatter(self.plain_formatter)
        self.time_lines = {}
        self.time_texts = {}
        colors = plt.cm.tab10(np.linspace(0, 1, len(self.channels)))
        for i, ch in enumerate(self.channels):
            self.time_lines[ch] = self.ax_temp_time.plot([], [], color=colors[i], lw=1.5, label=f'RTD {ch}')[0]
            self.time_texts[ch] = self.ax_temp_time.text(0.02 + (i * 0.09), 0.95, "",
                                                         transform=self.ax_temp_time.transAxes,
                                                         fontsize=9, color=colors[i], verticalalignment='top')
        self.ax_temp_time.legend(loc='lower left', fontsize='small')

        # --- Bottom Plot: Temperature vs. r/d ---
        self.ax_temp_rd.set_ylabel("Temperature [°C]")
        self.ax_temp_rd.set_xlabel("r/d")
        self.ax_temp_rd.set_xlim(self.config['plot_rd_min'], self.config['plot_rd_max'])
        self.ax_temp_rd.grid(True, linestyle='--')
        self.ax_temp_rd.yaxis.set_major_formatter(self.plain_formatter)
        self.rd_profile_line = self.ax_temp_rd.plot([], [], 'k--', lw=1, label='Profile')[0]
        self.rd_points = self.ax_temp_rd.plot([], [], 'o', color='red', mec='black', label='Sensors')[0]
        self.ax_temp_rd.legend(loc='lower left', fontsize='small')

    def _update_plots(self, frame):
        # This method preserves the original, complex logic for data retrieval and plotting
        new_data = self._get_new_data()
        if new_data is None:
            self.dmm.write('*TRG')
            return []

        self._process_and_store_data(new_data)

        # --- Update Top Plot (Temp vs. Time) ---
        elapsed_min = np.array(self.elapsed_seconds) / 60
        for ch in self.channels:
            if self.temperatures[ch]:
                self.time_lines[ch].set_data(elapsed_min, self.temperatures[ch])
                self.time_texts[ch].set_text(f"T{ch}: {self.temperatures[ch][-1]:.3f}°C")

        self.ax_temp_time.relim()
        self.ax_temp_time.autoscale_view()
        self.ax_temp_time.set_xlim(0, np.max(elapsed_min) + 2)

        all_current_temps = [temp for temp_list in self.temperatures.values() for temp in temp_list]
        if all_current_temps:
            min_temp = np.min(all_current_temps)
            max_temp = np.max(all_current_temps)
            delta_temp = max_temp - min_temp
            self.ax_temp_time.set_ylim(min_temp - delta_temp, max_temp + delta_temp)
        self.ax_temp_time.yaxis.set_major_formatter(self.plain_formatter)


        # --- Update Bottom Plot (Temp vs. r/d) ---
        if self.run_count > 0:
            base = self.config['rtd_first_coord']
            # Create x-coords for each sensor based on its index
            rd_x_points = np.array([base + 1.5 * i for i in range(len(self.channels))]) / 9.4
            # Get the latest temperature for each sensor
            rd_y_points = np.array([self.temperatures[ch][-1] for ch in self.channels])

            self.rd_points.set_data(rd_x_points, rd_y_points)
            self.rd_profile_line.set_data(rd_x_points, rd_y_points)  # Connects the points

            self.ax_temp_rd.set_xlim(np.min(rd_x_points) - 0.5, np.max(rd_x_points) + 0.5)

            # Dynamic Y-axis scaling for the r/d plot
            min_temp = np.min(rd_y_points)
            max_temp = np.max(rd_y_points)
            delta_temp = max_temp - min_temp
            self.ax_temp_rd.set_ylim(min_temp - delta_temp, max_temp + delta_temp)
            self.ax_temp_rd.yaxis.set_major_formatter(self.plain_formatter)

        # --- Trigger next measurement ---
        self.dmm.write('*TRG')
        return list(self.time_lines.values()) + [self.rd_profile_line, self.rd_points]

    def _get_new_data(self):
        """Gets only the new data added to the buffer since the last read."""
        try:
            # Find out how many total readings are in the buffer
            current_total_count = int(self.dmm.query("TRAC:ACT? 'defbuffer1'"))
            last_read_count = self.buffer_read_count[-1]

            if current_total_count <= last_read_count:
                return None  # No new data

            # Command to read from the last read point to the current end
            read_command = f"TRAC:DATA? {last_read_count + 1}, {current_total_count}, 'defbuffer1', READ, CHAN, REL"

            raw_data = self.dmm.query(read_command)

            self.buffer_read_count = np.append(self.buffer_read_count, current_total_count)
            return raw_data
        except (pyvisa.errors.VisaIOError, ValueError):
            return None

    def _process_and_store_data(self, raw_data):
        """Parses raw data, converts it, and stores it in dictionaries."""
        split_data = raw_data.strip().split(',')
        data_np = np.array(split_data, dtype=np.float64).reshape(-1, 3)

        # Calculate average resistance for each channel in the new data chunk
        avg_resistances = {}
        unique_channels = np.unique(data_np[:, 1])
        for ch_float in unique_channels:
            ch = int(ch_float)
            avg_resistances[ch] = np.mean(data_np[data_np[:, 1] == ch, 0])

        # Store data and log to the console
        self.run_count += 1
        now = datetime.datetime.now()
        self.timestamps.append(now.strftime('%H:%M:%S'))
        self.elapsed_seconds.append(self.config['trigger_interval_sec'] * (self.run_count - 1))

        log_line = f"{self.run_count:4d} | {self.timestamps[-1]:>10} | {self.elapsed_seconds[-1]:8.1f} |"

        for i, ch in enumerate(self.channels):
            resistance = avg_resistances.get(ch, 0.0)

            calib_col_index = i
            temperature = self._resistance_to_temperature(resistance, calib_col_index)

            self.resistances[ch].append(resistance)
            self.temperatures[ch].append(temperature)
            log_line += f" {temperature:10.3f} |"

        print(log_line)

    def _resistance_to_temperature(self, resistance, calib_col_index):
        # Preserves the user's original single-value conversion logic
        C = {0: 0.0, 1: 2.592800E-2, 2: -7.602961E-7, 3: 4.637791E-11,
             4: -2.165394E-15, 5: 6.048144E-20, 6: -7.293422E-25}

        a0 = self.calibration_coeffs[0, calib_col_index]
        a1 = self.calibration_coeffs[1, calib_col_index]
        a2 = self.calibration_coeffs[2, calib_col_index]

        normalization = 0.0
        if self.config['normalization_enabled']:
            norm_row = self.config['normalization_row_index']
            normalization = self.calibration_coeffs[norm_row-2, calib_col_index]

        if a2 != 0.0:  # Quadratic fitting
            discriminant = (a1 ** 2) - 4 * a2 * (a0 - resistance)
            if discriminant < 0: discriminant = 0
            volt = (-a1 + math.sqrt(discriminant)) / (2.0 * a2)
        else:  # Linear fitting
            discriminant = a0 - resistance
            volt = -discriminant/a1

        temp = sum(C[i] * volt ** i for i in range(7)) + normalization
        return temp

    def _save_data_to_excel(self):
        if not self.timestamps:
            print("\nℹ️  No data to save.")
            return

        df = pd.DataFrame({
            'Run': range(1, len(self.timestamps) + 1),
            'Time': self.timestamps,
            'Elapsed [sec]': self.elapsed_seconds,
        })
        for ch in self.channels:
            if ch in self.temperatures:
                df[f'T{ch}[°C]'] = self.temperatures[ch]
        for ch in self.channels:
            if ch in self.resistances:
                df[f'R{ch}[Ω]'] = self.resistances[ch]

        try:
            df.to_excel(f"{self.output_path}.xlsx", index=False)
            print(f"\n💾 Data saved successfully: {self.output_path}.xlsx")
        except Exception as e:
            print(f"❌ Failed to save Excel file: {e}")

    def _print_log_header(self):
        header = f"{'No.':>4} | {'Timestamp':>10} | {'Elapsed(s)':>8} |"
        for ch in self.channels:
            header += f" RTD{ch}      |"
        print(header)
        print("-" * len(header))

    def _close_dmm(self):
        print("\n🧹 Performing cleanup and shutdown...")
        if hasattr(self, 'dmm'):
            try:
                self.dmm.write("ABOR")
                print(f"✅ Final device status: {self.dmm.query('SYST:ERR?').strip()}")
                self.dmm.close()
                self.rm.close()
                print("✅ DAQ connection closed safely.")
            except Exception as e:
                print(f"⚠️ Error during DAQ cleanup: {e}")
        print("👋 Program finished.")


if __name__ == "__main__":
    # Define the channels to be used for this specific run

    # Use the wres context manager for high-resolution timing on Windows
    # On other OS, it will use a dummy that does nothing.
    try:
        import wres
    except ImportError:
        class DummyContextManager:
            def __enter__(self): pass

            def __exit__(self, exc_type, exc_val, exc_tb): pass


        wres = type('wres', (), {'set_resolution': lambda self, x: DummyContextManager()})()

    with wres.set_resolution(5000):
        logger = RealTimeDAQLogger(CONFIG)
        logger.run()
