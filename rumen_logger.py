"""
=============================================================
  RUMEN MONITOR — Python Logger v3
  Sensor aktif : DS18B20 (x1), DHT22, MH-Z14 (CO2),
                 Pressure (A0), TGS2611/CH4 (A1)

  Format CSV dari Arduino (7 field):
    temp1, dht_temp, dht_hum, co2, pt_adc, valve, tgs_adc, 
============================================================="""

import serial
import serial.tools.list_ports
import csv
import time
import logging
import os
from datetime import datetime
from collections import deque

# ==============================
#  KONFIGURASI UMUM
# ==============================
BAUD_RATE        = 9600

NO_DATA_TIMEOUT  = 150      # detik

RECONNECT_DELAY  = 5        # detik jeda antar percobaan reconnect
RECONNECT_TRIES  = 999      # jumlah percobaan reconnect sebelum menyerah

# ==============================
#  SETUP LOGGING KE FILE
#  Semua event error/reconnect dicatat ke file .log
#  sehingga bisa diinvestigasi setelah kejadian
# ==============================
log_fname = f"rumen_error_{datetime.now():%Y%m%d_%H%M%S}.log"
logging.basicConfig(
    filename=log_fname,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def log(msg, level="info"):
    """Print ke terminal sekaligus catat ke file log."""
    print(msg)
    if level == "info":
        logging.info(msg)
    elif level == "warning":
        logging.warning(msg)
    elif level == "error":
        logging.error(msg)

# ==============================
#  KONFIGURASI KALIBRASI
# ==============================

# ── DS18B20 (Suhu Rumen) ─────────────────────────────────────
TEMP1_A = 1.0133
TEMP1_B = - 0.4418

# ── DHT22 Suhu Lingkungan ────────────────────────────────────
DHTT_A = 0.8125
DHTT_B = 4.6748

# ── DHT22 Kelembapan ─────────────────────────────────────────
DHTH_A = 0.8731
DHTH_B = 15.213

# ── CO2 MH-Z14 ───────────────────────────────────────────────
CO2_A = 1.0
CO2_B = 0.0

# ── Tekanan / Pressure (A0) ──────────────────────────────────
PRESS_A = 255.1
PRESS_B = -143112

# ── TGS2611 (Metana / CH4) (A1) ─────────────────────────────
TGS_VCC = 5.0
TGS_RL  = 4700.0
TGS_RO  = 2596.05
TGS_A   = 133.4
TGS_B   = -0.5752

# ==============================
#  FUNGSI KALIBRASI
# ==============================
def cal_temp1(raw: float) -> float:
    return TEMP1_A * raw + TEMP1_B

def cal_dht_temp(raw: float) -> float:
    return DHTT_A * raw + DHTT_B

def cal_dht_hum(raw: float) -> float:
    return max(0.0, min(100.0, DHTH_A * raw + DHTH_B))

def cal_co2(raw: int) -> float:
    return CO2_A * raw + CO2_B

def cal_pressure(pt_adc: float) -> float:
    pa = PRESS_A * pt_adc + PRESS_B
    return pa / 1000.0

def cal_tgs(tgs_adc: float) -> float:
    vout = tgs_adc / 1023.0 * TGS_VCC
    if vout <= 0:
        return 0.0, 0.0
    rs    = TGS_RL * (TGS_VCC - vout) / vout
    ratio = rs / TGS_RO
    ppm   = 0.0
    if TGS_A > 0 and TGS_B != 0 and ratio > 0:
        try:
            ppm = pow(ratio / TGS_A, 1.0 / TGS_B)
        except (ValueError, ZeroDivisionError):
            ppm = 0.0
    return ratio, ppm


# ==============================
#  AUTO DETECT PORT
# ==============================
def find_port() -> str | None:
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        log("❌ Tidak ada serial port ditemukan.", "warning")
        return None
    log("\nScanning serial ports...")
    for p in ports:
        log(f"  Found: {p.device} | {p.description}")
        desc = p.description.lower()
        if any(k in desc for k in ("usb", "arduino", "ch340", "cp210")):
            log(f"✅ Arduino dipilih: {p.device}", "info")
            return p.device
    log("⚠  Arduino tidak terdeteksi otomatis.", "warning")
    return input("Masukkan COM/port manual (contoh COM5 atau /dev/ttyUSB0): ").strip()


# ==============================
#  KONEKSI SERIAL DENGAN SCAN ULANG
# ==============================
def connect_serial(baud: int, retries: int = RECONNECT_TRIES) -> serial.Serial | None:
    """
    Coba temukan dan buka port serial.
    Scan ulang port setiap percobaan — tidak hardcode nama port.
    Return objek Serial jika berhasil, None jika semua percobaan gagal.
    """
    for i in range(retries):
        port = find_port()
        if port:
            try:
                s = serial.Serial(port, baud, timeout=1)
                time.sleep(2)
                msg = f"Serial terhubung: {port} @ {baud} baud"
                log(f"✅ {msg}", "info")
                return s
            except Exception as e:
                log(f"🔄 Reconnect attempt {i+1}: gagal buka {port} — {e}", "warning")
        else:
            log(f"🔄 Reconnect attempt {i+1}: port tidak ditemukan", "warning")
        time.sleep(RECONNECT_DELAY)
    log("🚨 Semua percobaan reconnect gagal.", "error")
    return None


# ==============================
#  KONEKSI AWAL
# ==============================
port = find_port()
if not port:
    log("🚨 Tidak ada port — program berhenti.", "error")
    exit(1)

try:
    ser = serial.Serial(port, BAUD_RATE, timeout=1)
except Exception as e:
    log(f"🚨 Gagal membuka port serial: {e}", "error")
    exit(1)

time.sleep(2)
log(f"\n✅ Serial terhubung: {port} @ {BAUD_RATE} baud", "info")


# ==============================
#  CSV OUTPUT
# ==============================
fname = f"rumen_log_{datetime.now():%Y%m%d_%H%M%S}.csv"
csvfile = open(fname, 'w', newline='', encoding='utf-8')
writer  = csv.writer(csvfile)

writer.writerow([
    "Timestamp",
    "Temp1_raw_C",       "Temp1_cal_C",
    "EnvTemp_raw_C",     "EnvTemp_cal_C",
    "EnvHum_raw_pct",    "EnvHum_cal_pct",
    "CO2_raw_ppm",       "CO2_cal_ppm",
    "PT_raw_ADC",        "Press_cal_kPa",
    "Valve_State",
    "TGS_raw_ADC",       "TGS_RsRo",       "TGS_ppm_powlaw",
])

# ==============================
#  HEADER MONITOR TERMINAL
# ==============================
HEADER = (
    f"{'Waktu':>8} | "
    f" {'T1cal':>5} | "
    f"{'Tenv':>5} {'RH%':>5} | "
    f"{'CO2cal':>7} | "
    f"{'PTadc':>5} {'kPa':>8} | "
    f"{'Valve':>6} | "
    f"{'TGSadc':>6} {'ppm':>8} | "
)

print(f"\n{'='*110}")
print("  RUMEN MONITOR v3 — LOGGING AKTIF")
print(f"  Data CSV : {fname}")
print(f"  Error log: {log_fname}")
print(f"  Timeout  : {NO_DATA_TIMEOUT}s (Arduino kirim per 60s)")
print(f"{'='*110}")
print(HEADER)
print("-" * 110)


# ==============================
#  MAIN LOOP
# ==============================
EXPECTED_FIELDS = 7
last_data_time  = time.time()   # catat kapan terakhir data valid masuk

while True:
    try:

        # ── Cek timeout data ──────────────────────────────────
        # Kalau sudah NO_DATA_TIMEOUT detik tidak ada data masuk,
        elapsed = time.time() - last_data_time
        if elapsed > NO_DATA_TIMEOUT:
            log(
                f"⚠  Timeout: tidak ada data selama {elapsed:.0f}s "
                f"(batas {NO_DATA_TIMEOUT}s) — reconnect...",
                "warning"
            )
            try:
                ser.close()
            except Exception:
                pass
            ser = connect_serial(BAUD_RATE)
            if ser is None:
                log("🚨 Gagal reconnect setelah timeout — logging berhenti.", "error")
                break
            last_data_time = time.time()   # reset timer setelah reconnect
            continue

        # ── Baca data dari serial ─────────────────────────────
        raw_line = ser.readline().decode(errors='ignore').strip()
        if not raw_line:
            continue    # Arduino belum kirim — tunggu, jangan reset timer

        if raw_line == 'STATUS:RUN':
           log("▶  Arduino: RUNNING", "info")
           continue

        if raw_line == 'STATUS:STOP':
           log("■  Arduino: STOP", "info")
           continue

        parts = raw_line.split(',')
        if len(parts) != EXPECTED_FIELDS:
            log(f"⚠  Data tidak valid ({len(parts)} field): {raw_line}", "warning")
            continue    # baris tidak valid — jangan reset timer

        # ── Parse ─────────────────────────────────────────────
        t1_raw_str, te_raw_str, he_raw_str, \
        co2_raw_str, pt_raw_str, valve_raw, \
        tgs_raw_str = parts

        t1_raw  = float(t1_raw_str)
        te_raw  = float(te_raw_str)
        he_raw  = float(he_raw_str)
        co2_raw = int(co2_raw_str)
        pt_raw  = int(pt_raw_str)
        valve   = "OPEN" if valve_raw.strip() == "1" else "CLOSED"
        tgs_raw = int(tgs_raw_str)

        # Data valid diterima — update timer
        last_data_time = time.time()

        # ── Kalibrasi ─────────────────────────────────────────
        t1_cal             = cal_temp1(t1_raw)
        te_cal             = cal_dht_temp(te_raw)
        he_cal             = cal_dht_hum(he_raw)
        co2_cal            = cal_co2(co2_raw)
        p_cal_kpa          = cal_pressure(pt_raw)
        tgs_ratio, tgs_ppm = cal_tgs(tgs_raw)

        # ── Tulis CSV ─────────────────────────────────────────
        # csvfile.flush() dipanggil setiap baris agar data tidak
        # hilang kalau program tiba-tiba crash
        ts = datetime.now().strftime("%H:%M:%S")
        writer.writerow([
            ts,
            round(t1_raw, 2),      round(t1_cal, 2),
            round(te_raw, 2),      round(te_cal, 2),
            round(he_raw, 1),      round(he_cal, 1),
            co2_raw,               round(co2_cal, 1),
            pt_raw,                round(p_cal_kpa, 3),
            valve,
            tgs_raw,               round(tgs_ratio, 4),   round(tgs_ppm, 2),
        ])
        csvfile.flush()     # pastikan data langsung tersimpan ke disk
        os.fsync(csvfile.fileno())

        # ── Terminal ──────────────────────────────────────────
        print(
            f"{ts:>8} | "
            f"{t1_cal:>5.1f} | "
            f"{te_cal:>5.1f} {he_cal:>5.1f} | "
            f"{co2_cal:>7.1f} | "
            f"{round(pt_raw):>6d} {p_cal_kpa:>8.3f} | "
            f"{valve:>6} | "
            f"{round(tgs_raw):>7d} {tgs_ratio:>6.4f} {tgs_ppm:>8.2f} | "
        )

    except KeyboardInterrupt:
        log("\n🛑 Logging dihentikan oleh user.", "info")
        break

    except ValueError as e:
        log(f"⚠  Gagal konversi nilai: {e} — baris: {raw_line!r}", "warning")
        # Tidak reconnect — ini error parsing, bukan error koneksi

    except Exception as e:
        # Error koneksi serial — coba reconnect
        # CSV tetap file yang sama, tidak dibuat baru
        log(f"🚨 Error koneksi: {e} — mencoba reconnect...", "error")
        try:
            ser.close()
        except Exception:
            pass
        time.sleep(RECONNECT_DELAY)
        ser = connect_serial(BAUD_RATE)
        if ser is None:
            log("🚨 Gagal reconnect — logging berhenti.", "error")
            break
        last_data_time = time.time()   # reset timer setelah reconnect berhasil
        log("✅ Reconnect berhasil — logging lanjut ke file yang sama!", "info")

# ==============================
#  CLEANUP
# ==============================
try:
    ser.close()
except Exception:
    pass
csvfile.close()
log(f"\n✅ Data tersimpan di: {fname}", "info")
log(f"📋 Error log tersimpan di: {log_fname}", "info")
print("Program selesai.")
