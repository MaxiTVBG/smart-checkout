import os
from pylibdmtx.pylibdmtx import encode
from PIL import Image
import argparse

def generate_codes(item_class, count, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    print(f"Генериране на {count} Data Matrix кода за клас '{item_class}'...")
    
    for i in range(1, count + 1):
        # Уникален идентификатор във формат class_seed
        uid = f"{item_class}_{1000 + i}"
        
        # Енкодинг на данните
        encoded = encode(uid.encode('utf-8'))
        
        # Създаване на изображение от пикселите
        img = Image.frombytes('RGB', (encoded.width, encoded.height), encoded.pixels)
        
        # Увеличаване на мащаба (scaling) за по-добро принтиране, иначе кодът е 14x14 пиксела
        img = img.resize((encoded.width * 10, encoded.height * 10), Image.NEAREST)
        
        file_path = os.path.join(output_dir, f"{uid}.png")
        img.save(file_path)
        print(f"Създаден: {file_path}")
        
    print(f"\n✅ Всички кодове са генерирани успешно в папка '{output_dir}'!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Генератор на Data Matrix кодове")
    parser.add_argument("--cls", type=str, required=True, help="Класът на обекта (напр. 'multicet', 'kabel')")
    parser.add_argument("--count", type=int, default=10, help="Брой кодове за генериране (по подразбиране: 10)")
    parser.add_argument("--out", type=str, default="datamatrix_codes", help="Изходна папка за снимките")
    
    args = parser.parse_args()
    
    # Ако пътят не е абсолютен, създай го спрямо директорията, от която се стартира скрипта
    out_dir = os.path.abspath(args.out)
    generate_codes(args.cls, args.count, out_dir)
