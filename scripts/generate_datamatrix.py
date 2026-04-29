import os
import sqlite3
import sys
import yaml
from pylibdmtx.pylibdmtx import encode
from PIL import Image
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.database import InventoryDatabase
from src.secure_codes import (
    SecureCodeError,
    create_secure_payload,
    load_code_secret,
    verify_secure_payload,
)


def load_config(config_path):
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)


def generate_codes(item_class, count, output_dir, inventory_db, secret):
    os.makedirs(output_dir, exist_ok=True)
    print(f"Генериране и регистриране на {count} защитени Data Matrix кода за клас '{item_class}'...")
    
    for i in range(1, count + 1):
        secure_code = _create_and_register_code(item_class, inventory_db, secret)

        # Енкодинг на данните
        encoded = encode(secure_code.payload.encode('utf-8'))
        
        # Създаване на изображение от пикселите
        img = Image.frombytes('RGB', (encoded.width, encoded.height), encoded.pixels)
        
        # Увеличаване на мащаба (scaling) за по-добро принтиране, иначе кодът е 14x14 пиксела
        img = img.resize((encoded.width * 10, encoded.height * 10), Image.NEAREST)
        
        file_path = os.path.join(output_dir, f"{secure_code.inventory_uid}.png")
        img.save(file_path)
        print(f"Създаден и регистриран: {file_path}")
        
    print(f"\n✅ Всички кодове са генерирани успешно в папка '{output_dir}'!")


def _create_and_register_code(item_class, inventory_db, secret, max_attempts=20):
    for _ in range(max_attempts):
        payload = create_secure_payload(item_class, secret)
        secure_code = verify_secure_payload(payload, secret)

        try:
            inventory_db.register_code(
                secure_code.public_uid,
                secure_code.payload,
                secure_code.item_class,
            )
            return secure_code
        except sqlite3.IntegrityError:
            continue

    raise RuntimeError("Неуспешно генериране на уникален код след много опити.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Генератор на Data Matrix кодове")
    parser.add_argument("--cls", type=str, required=True, help="Класът на обекта (напр. 'multicet', 'kabel')")
    parser.add_argument("--count", type=int, default=10, help="Брой кодове за генериране (по подразбиране: 10)")
    parser.add_argument("--out", type=str, default="datamatrix_codes", help="Изходна папка за снимките")
    parser.add_argument("--config", type=str, default="config.yaml", help="Път до config.yaml")
    parser.add_argument("--db", type=str, default=None, help="SQLite база данни. По подразбиране се взима от config.yaml")
    
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        secret = load_code_secret(config)
    except (OSError, SecureCodeError) as e:
        parser.error(str(e))
    
    # Ако пътят не е абсолютен, създай го спрямо директорията, от която се стартира скрипта
    out_dir = os.path.abspath(args.out)
    db_path = os.path.abspath(args.db or config['paths']['db_path'])
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    inventory_db = InventoryDatabase(db_path)

    try:
        generate_codes(args.cls, args.count, out_dir, inventory_db, secret)
    except (RuntimeError, SecureCodeError) as e:
        parser.error(str(e))
    finally:
        inventory_db.close()
