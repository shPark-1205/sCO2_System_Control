import sys
import os
import time
import datetime
import pyvisa
import numpy as np
import pandas as pd
import math

# =================================================================================
# ⚙️ Experiment Configuration
# =================================================================================
CONFIG = {
    # --- Experiment Info ---
    "exp_objective": "20260109",
    # "exp_sub_objective": "7.771MPa_31.3C_40000_16V_Hd1.00-r60mm",
    "exp_sub_objective": "jet-heater-on-steady-new_correction",

    # --- Measurement Settings ---
    "logging_duration_min": 1,  # Total time to log data in minutes
    "nplc": 2.0,  # Number of Power Line Cycles for integration

    # --- Instrument & Channel Settings ---
    "visa_resource_name": "USB0::0x05E6::0x6510::04444312::0::INSTR",  # RTD DAQ Address
    # "rtd_channels_str": "103, 104, 113, 106, 107, 114, 109, 110, 111, 112",  # RTD DAQ channels
    "rtd_channels_str": "104, 113, 106, 107, 114, 109",

    # --- Calibration & Normalization ---
    "calibration_file_path": './A_calibration_curve_RTD_poly_final_Quadratic_2to7RTD_new3.xlsx',
    "normalization_enabled": True,  # True to apply normalization, False to disable
    # Row index in the calibration file for normalization value (3:vacuum, 4:pool, etc.)
    # Refer to calibration_curve.xlsx file
    "normalization_row_index": 7,
}


# =================================================================================

class DAQLogger:
    """
    Logs RTD sensor data from a Keithley DAQ6510 for a specified duration,
    processes the data, and saves it to an Excel file.
    """

    def __init__(self, config):
        """Initializes the logger, loads calibration data, and connects to the DAQ."""
        self.config = config
        self.channels = self.config['rtd_channels_str'].split(', ')
        self.num_sensors = len(self.channels)
        self.processed_data = {}  # Dictionary to store final dataframes

        self._load_calibration_data()
        self._connect_dmm()

    def run(self):
        """Executes the entire logging and saving process."""
        try:
            self._configure_dmm()
            raw_data_string = self._execute_logging_cycle()
            self._process_raw_data(raw_data_string)
            self._save_data_to_excel()
        except Exception as e:
            print(f"\n❌ An unexpected error occurred: {e}")
        finally:
            self._close_dmm()

    def _load_calibration_data(self):
        """Loads the RTD calibration coefficients from an Excel file."""
        try:
            print(f"🔄 Loading calibration file: {self.config['calibration_file_path']}")
            fit_df = pd.read_excel(self.config['calibration_file_path'])
            self.calibration_coeffs = fit_df.values
            print("✅ Calibration data loaded successfully.")
        except FileNotFoundError:
            print(f"❌ ERROR: Calibration file not found at '{self.config['calibration_file_path']}'")
            sys.exit(1)

    def _connect_dmm(self):
        """Connects to the DAQ6510 instrument via VISA."""
        try:
            print("🔄 Connecting to DAQ...")
            self.rm = pyvisa.ResourceManager()
            self.daq = self.rm.open_resource(self.config['visa_resource_name'])
            self.daq.write_termination = "\n"
            self.daq.timeout = 30000  # Increased timeout for potentially long data transfers

            self.daq.write("ABOR")  # Ensure any previous scan is stopped
            print(f"✅ Device connected successfully: {self.daq.query('*IDN?').strip()}")
            print(f"✅ Device status check: {self.daq.query('SYST:ERR?').strip()}")
            self.daq.write("SYST:CLE")  # Clear system error queue
        except pyvisa.errors.VisaIOError as e:
            print(f"❌ DAQ connection failed. Check VISA address and connection.\n   Error: {e}")
            sys.exit(1)

    def _configure_dmm(self):
        """Sends configuration commands to the DAQ."""
        channels = self.config['rtd_channels_str']
        settings = [
            "*RST",
            "DISP:SCR HOME",
            "TRAC:CLE 'defbuffer1'",
            f"SENS:FUNC 'FRES',(@{channels})",
            f"SENS:FRES:RANG 1000,(@{channels})",
            f"SENS:FRES:AZER ON,(@{channels})",
            f"SENS:FRES:NPLC {self.config['nplc']},(@{channels})",
            f"SENS:FRES:OCOM OFF,(@{channels})",
            "ROUT:SCAN:MEAS:INT 0",
            "ROUT:SCAN:INT 0",
            "ROUT:SCAN:COUNT:SCAN 0",
            f"ROUT:SCAN:CRE (@{channels})",
            "ROUT:SCAN:BUFF 'defbuffer1'"
        ]
        print("⚙️  Applying DAQ settings...")
        for cmd in settings:
            self.daq.write(cmd)
            time.sleep(0.05)
        print("✅ DAQ configuration complete.")
        self.daq.write("DISP:SCR PROC")

    def _execute_logging_cycle(self):
        """Starts the scan, waits for the specified duration, and retrieves the data."""
        logging_seconds = self.config['logging_duration_min'] * 60

        self.daq.write("INIT")  # Start scanning and storing data in the buffer

        self.start_time_str = datetime.datetime.now().strftime('%H:%M:%S')
        print(f"\n▶️  Logging started at: {self.start_time_str}")
        print(f"   Waiting for {self.config['logging_duration_min']} minute(s)... Please wait.")

        time.sleep(logging_seconds)

        self.daq.write("ABOR")  # Stop the scan
        self.end_time_str = datetime.datetime.now().strftime('%H:%M:%S')
        print(f"⏹️  Logging finished at: {self.end_time_str}")

        print("🔄 Retrieving data from DAQ buffer...")
        # Get the number of readings stored in the buffer
        num_readings = int(self.daq.query("TRAC:ACT? 'defbuffer1'"))
        if num_readings == 0:
            print("⚠️ Warning: No data was collected in the buffer.")
            return ""

        # Command to read all data (reading, channel, relative time)
        read_command = f"TRAC:DATA? 1, {num_readings}, 'defbuffer1', READ, CHAN, REL"
        return self.daq.query(read_command)

    def _process_raw_data(self, raw_data_string):
        """Processes the raw data string from the DAQ into structured dataframes."""
        if not raw_data_string:
            print("No data to process.")
            return

        print("🔬 Processing raw data...")
        # Split the string and reshape into [Reading, Channel, Time]
        split_data = raw_data_string.strip().split(',')
        data_np = np.array(split_data, dtype=np.float64).reshape(-1, 3)

        # Get unique channels while preserving the original order
        unique_channels = sorted(list(dict.fromkeys(data_np[:, 1])), key=lambda x: self.channels.index(str(int(x))))

        for i, channel_num_float in enumerate(unique_channels):
            channel_num = int(channel_num_float)

            # Filter data for the current channel
            mask = data_np[:, 1] == channel_num
            time_data = data_np[mask, 2]
            resistance_data = data_np[mask, 0]

            # Convert resistance to temperature using the provided logic
            temp_data = self._resistance_to_temperature(resistance_data, i)

            # Store data in pandas DataFrames within a dictionary
            self.processed_data[channel_num] = {
                'time': pd.DataFrame(time_data, columns=[f't_{channel_num}[s]']),
                'raw_resistance': pd.DataFrame(resistance_data, columns=[f'R_{channel_num}[Ω]']),
                'temperature': pd.DataFrame(temp_data, columns=[f'T_{channel_num}[℃]'])
            }
        print("✅ Data processing complete.")

    def _resistance_to_temperature(self, resistance_array, sensor_index):
        """Converts an array of resistance values to temperature using calibration data."""
        temp_array = np.array([])
        C = {0: 0.0, 1: 2.592800E-2, 2: -7.602961E-7, 3: 4.637791E-11,
             4: -2.165394E-15, 5: 6.048144E-20, 6: -7.293422E-25}  # T-type TC Inverse polynomials

        a0 = self.calibration_coeffs[0, sensor_index]
        a1 = self.calibration_coeffs[1, sensor_index]
        a2 = self.calibration_coeffs[2, sensor_index]

        normalization_val = 0.0
        if self.config['normalization_enabled']:
            norm_row = self.config['normalization_row_index']
            normalization_val = self.calibration_coeffs[norm_row-2, sensor_index]

        for R_measure in resistance_array:

            if a2 != 0.0:  # Quadratic fitting
                discriminant = (a1 ** 2) - 4 * a2 * (a0 - R_measure)
                if discriminant < 0: discriminant = 0
                volt = (-a1 + math.sqrt(discriminant)) / (2.0 * a2)
            else:  # Linear fitting
                discriminant = a0 - R_measure
                volt = -discriminant / a1

            # # Solve quadratic equation for voltage
            # discriminant = (a1 ** 2) - (4 * a2 * (a0 - R_measure))
            # if discriminant < 0: discriminant = 0  # Avoid math domain error
            # volt = (-a1 + math.sqrt(discriminant)) / (2.0 * a2)  # R -> V

            # Calculate temperature from voltage using polynomial
            temp = sum(C[i] * volt ** i for i in range(7)) + normalization_val  # R -> V -> T
            temp_array = np.append(temp_array, temp)

        return temp_array

    def _save_data_to_excel(self):
        """Saves the processed data to an Excel file."""
        if not self.processed_data:
            print("ℹ️  No data to save.")
            return

        print("💾 Saving data to Excel file...")
        time_title = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        path_title = f"data/_RTD-{self.config['exp_objective']}"
        sub_path = f"{path_title}/_RTD-{self.config['exp_sub_objective']}"
        output_path = f"{sub_path}/_RTD-{self.config['exp_sub_objective']}_{time_title}.xlsx"

        try:
            os.makedirs(sub_path, exist_ok=True)

            # Create a list of all dataframes to be concatenated
            main_dfs = []
            for data_type in ['temperature', 'time', 'raw_resistance']:
                for ch_num_str in self.channels:
                    ch_num = int(ch_num_str)
                    if ch_num in self.processed_data:
                        main_dfs.append(self.processed_data[ch_num][data_type])

            # Step 2: Concatenate the main data
            if not main_dfs:
                print("No main data to save.")
                return
            final_df = pd.concat(main_dfs, axis=1)

            # Step 3: Create the new first column
            info_column_data = [
                f"{self.start_time_str}",
                f"{self.end_time_str}"
            ]
            # Pad the list with empty strings to match the length of the DataFrame
            padding = [""] * (len(final_df) - len(info_column_data))
            info_column_data.extend(padding)

            # Step 4: Insert the new column at the beginning (index 0)
            final_df.insert(0, 'Time', pd.Series(info_column_data))

            # Step 5: Save to Excel
            final_df.to_excel(output_path, index=False)
            print(f"✅ Data saved successfully: {output_path}")

        except Exception as e:
            print(f"❌ Failed to save Excel file: {e}")

    def _close_dmm(self):
        """Closes the connection to the DAQ safely."""
        print("🧹 Performing cleanup and shutdown procedures.")
        if hasattr(self, 'daq'):
            try:
                self.daq.write("DISP:SCR HOME")
                print(f"✅ Final device status: {self.daq.query('SYST:ERR?').strip()}")
                self.daq.close()
                self.rm.close()
                print("✅ DAQ connection closed safely.")
            except pyvisa.errors.VisaIOError as e:
                print(f"⚠️ Error during DAQ cleanup: {e}")
        print("👋 Program finished.")


if __name__ == "__main__":
    logger = DAQLogger(CONFIG)
    logger.run()