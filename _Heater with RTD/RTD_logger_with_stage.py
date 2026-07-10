import sys
import os
import time
import datetime
import pyvisa
import numpy as np
import pandas as pd
import math


# =================================================================================
# 🌡️ RTD 로거 클래스 (모듈화)
# =================================================================================
class DAQLogger:
    def __init__(self, config):
        """
        초기화 함수
        :param config: 계측 설정을 담은 딕셔너리
        """
        self.config = config
        self.channels = self.config['rtd_channels_str'].split(', ')
        self.processed_data = {}
        self.calibration_coeffs = None

        self._load_calibration_data()

        # Visa 리소스 매니저는 한 번만 생성
        self.rm = pyvisa.ResourceManager()
        self.daq = None

    def _load_calibration_data(self):
        try:
            fit_df = pd.read_excel(self.config['calibration_file_path'])
            self.calibration_coeffs = fit_df.values
            # print("✅ Calibration data loaded.")
        except FileNotFoundError:
            print(f"❌ ERROR: Calibration file not found: {self.config['calibration_file_path']}")
            sys.exit(1)

    def connect(self):
        """DAQ 장비 연결"""
        try:
            self.daq = self.rm.open_resource(self.config['visa_resource_name'])
            self.daq.write_termination = "\n"
            self.daq.timeout = 30000 + (self.config['logging_duration_min'] * 60 * 1000)
            self.daq.write("ABOR")
            self.daq.write("SYST:CLE")
            # print("✅ DAQ Connected.")
            return True
        except pyvisa.errors.VisaIOError as e:
            print(f"❌ DAQ Connect Fail: {e}")
            return False

    def disconnect(self):
        """DAQ 장비 연결 해제"""
        if self.daq:
            try:
                self.daq.write("DISP:SCR HOME")
                self.daq.close()
            except:
                pass
        # self.rm.close() # RM은 닫지 않음 (반복 사용 위해)
        # print("🔌 DAQ Disconnected.")

    def execute_logging(self, current_tag_name):
        """
        계측 실행 메인 함수
        :param current_tag_name: 현재 파일명에 붙을 태그 (예: "-r12mm")
        """
        # 현재 실행을 위한 sub_objective 업데이트
        current_sub_objective = f"{self.config['base_sub_objective']}{current_tag_name}"

        try:
            self._configure_dmm()

            # 실제 계측 수행
            raw_data = self._execute_logging_cycle(current_sub_objective)

            # 데이터 처리 및 저장
            self._process_raw_data(raw_data)
            saved_path = self._save_data_to_excel(current_sub_objective)

            return saved_path

        except Exception as e:
            print(f"❌ Logging Error: {e}")
            return None

    def _configure_dmm(self):
        channels = self.config['rtd_channels_str']
        settings = [
            "*RST", "DISP:SCR HOME", "TRAC:CLE 'defbuffer1'",
            f"SENS:FUNC 'FRES',(@{channels})",
            f"SENS:FRES:RANG 1000,(@{channels})",
            f"SENS:FRES:AZER ON,(@{channels})",
            f"SENS:FRES:NPLC {self.config['nplc']},(@{channels})",
            f"SENS:FRES:OCOM OFF,(@{channels})",
            "ROUT:SCAN:MEAS:INT 0", "ROUT:SCAN:INT 0", "ROUT:SCAN:COUNT:SCAN 0",
            f"ROUT:SCAN:CRE (@{channels})", "ROUT:SCAN:BUFF 'defbuffer1'"
        ]
        for cmd in settings:
            self.daq.write(cmd)
            time.sleep(0.05)
        self.daq.write("DISP:SCR PROC")

    def _execute_logging_cycle(self, sub_obj_name):
        logging_seconds = self.config['logging_duration_min'] * 60

        print(f"   ▶️  Logging Start: {sub_obj_name} ({self.config['logging_duration_min']} min)...")
        self.daq.write("INIT")
        self.start_time_str = datetime.datetime.now().strftime('%H:%M:%S')

        time.sleep(logging_seconds)

        self.daq.write("ABOR")
        self.end_time_str = datetime.datetime.now().strftime('%H:%M:%S')
        print("   ⏹️  Logging Finished.")

        num_readings = int(self.daq.query("TRAC:ACT? 'defbuffer1'"))
        if num_readings == 0: return ""
        return self.daq.query(f"TRAC:DATA? 1, {num_readings}, 'defbuffer1', READ, CHAN, REL")

    def _process_raw_data(self, raw_data_string):
        if not raw_data_string: return
        self.processed_data = {}  # 초기화
        split_data = raw_data_string.strip().split(',')
        data_np = np.array(split_data, dtype=np.float64).reshape(-1, 3)
        unique_channels = sorted(list(dict.fromkeys(data_np[:, 1])), key=lambda x: self.channels.index(str(int(x))))

        for i, channel_num_float in enumerate(unique_channels):
            channel_num = int(channel_num_float)
            mask = data_np[:, 1] == channel_num
            time_data = data_np[mask, 2]
            resistance_data = data_np[mask, 0]
            temp_data = self._resistance_to_temperature(resistance_data, i)
            self.processed_data[channel_num] = {
                'time': pd.DataFrame(time_data, columns=[f't_{channel_num}[s]']),
                'raw_resistance': pd.DataFrame(resistance_data, columns=[f'R_{channel_num}[Ω]']),
                'temperature': pd.DataFrame(temp_data, columns=[f'T_{channel_num}[℃]'])
            }

    def _resistance_to_temperature(self, resistance_array, sensor_index):
        temp_array = np.array([])
        C = {0: 0.0, 1: 2.592800E-2, 2: -7.602961E-7, 3: 4.637791E-11,
             4: -2.165394E-15, 5: 6.048144E-20, 6: -7.293422E-25}
        a0 = self.calibration_coeffs[0, sensor_index]
        a1 = self.calibration_coeffs[1, sensor_index]
        a2 = self.calibration_coeffs[2, sensor_index]
        normalization_val = 0.0
        if self.config['normalization_enabled']:
            norm_row = self.config['normalization_row_index']
            normalization_val = self.calibration_coeffs[norm_row - 2, sensor_index]

        for R_measure in resistance_array:
            if a2 != 0.0:
                discriminant = (a1 ** 2) - 4 * a2 * (a0 - R_measure)
                if discriminant < 0: discriminant = 0
                volt = (-a1 + math.sqrt(discriminant)) / (2.0 * a2)
            else:
                discriminant = a0 - R_measure
                volt = -discriminant / a1
            temp = sum(C[i] * volt ** i for i in range(7)) + normalization_val
            temp_array = np.append(temp_array, temp)
        return temp_array

    def _save_data_to_excel(self, sub_obj_name):
        if not self.processed_data: return None
        time_title = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

        # 경로 생성
        path_title = f"data/_RTD-{self.config['base_exp_objective']}"
        sub_path = f"{path_title}/_RTD-{sub_obj_name}"
        output_path = f"{sub_path}/_RTD-{sub_obj_name}_{time_title}.xlsx"

        try:
            os.makedirs(sub_path, exist_ok=True)
            main_dfs = []
            for data_type in ['temperature', 'time', 'raw_resistance']:
                for ch_num_str in self.channels:
                    ch_num = int(ch_num_str)
                    if ch_num in self.processed_data:
                        main_dfs.append(self.processed_data[ch_num][data_type])
            if not main_dfs: return None

            final_df = pd.concat(main_dfs, axis=1)
            info_column_data = [f"{self.start_time_str}", f"{self.end_time_str}"]
            padding = [""] * (len(final_df) - len(info_column_data))
            info_column_data.extend(padding)
            final_df.insert(0, 'Time', pd.Series(info_column_data))
            final_df.to_excel(output_path, index=False)
            print(f"   💾 Saved: {output_path}")
            return output_path
        except Exception as e:
            print(f"   ❌ Save Fail: {e}")
            return None