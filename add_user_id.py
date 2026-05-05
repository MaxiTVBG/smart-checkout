from time import sleep

from src.rfid_reader import RFIDReader;
from src.database import InventoryDatabase;
from main import load_config

def main():
    config = load_config()
    db = InventoryDatabase(config['paths']['db_path'])
    rfid = RFIDReader(config['raspberry']['rfid_rst_pin'])
    while True:
        uid = rfid.get_last_scan()
        if uid:
            print(f"Scanned UID: {uid}")
            name = input("Enter name for this UID: ")
            db.add_user(uid, name)

        sleep (0.1)

if __name__ == "__main__":
    main()

        
            