#!/usr/bin/env python3
# -*- coding: utf8 -*-

import time
import threading
from . import MFRC522

class RFIDReader:
    def __init__(self, rst_pin):
        """
        Initializes the MFRC522 reader and starts the background scanning thread.
        """
        self.reader = MFRC522.MFRC522(rst_pin=rst_pin)
        self._last_uid = None
        self._running = True
        self.failures = 0
        
        # Start the background thread so it doesn't block the YOLO video feed
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()
        print("[RFID] Background scanner started successfully.")

    def _scan_loop(self):
        """
        This is the infinite loop that runs invisibly in the background.
        It constantly checks for a card.
        """
        while self._running:
            # Step 1: Scan for cards (Non-blocking check)
            (status, tag_type) = self.reader.request(self.reader.PICC_REQIDL)

            # If a card is found
            if status == self.reader.MI_OK:
                # Step 2: Get the Unique Hardware ID (UID) of the card
                (status, uid) = self.reader.select_tag_sn()

                if status == self.reader.MI_OK:
                    # Convert the UID list of numbers into a clean Hex String (e.g., "A1B2C3D4")
                    uid_str = ''.join([f'{i:02X}' for i in uid])
                    
                    # Store it so the main YOLO script can grab it
                    self._last_uid = uid_str
                    
                    # Anti-Spam: Wait 2 seconds before accepting another scan 
                    # so the system doesn't register 50 scans from holding the card there.
                    time.sleep(2.0)
            else:
                self.failures += 1
                if self.failures >= 30:
                    print("[RFID] No card detected after 10 attempts. Resetting reader.")
                    self.reader.hard_reset()
                    self.failures = 0

            # A tiny sleep to prevent the background thread from using 100% of the CPU
            time.sleep(1.0) 

    def get_last_scan(self):
        """
        This is the method your main.py calls every frame.
        It acts like a mailbox: if there is a UID, it returns it and empties the mailbox.
        """
        if self._last_uid is not None:
            uid = self._last_uid
            self._last_uid = None  # Clear it after reading so we don't process it twice
            return uid
        return None

    def stop(self):
        """
        Gracefully shuts down the background thread when the program closes.
        """
        self._running = False
        self._thread.join()
        print("[RFID] Scanner stopped.")

# --- Quick Test Block ---
# If you run this file directly (python rfid_reader.py), it will test the reader.
if __name__ == '__main__':
    try:
        print("Testing RFID Reader... Press Ctrl+C to stop.")
        scanner = RFIDReader()
        while True:
            chip_id = scanner.get_last_scan()
            if chip_id:
                print(f"Scanned Chip UID: {chip_id}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        scanner.stop()