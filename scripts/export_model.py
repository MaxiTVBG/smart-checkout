from ultralytics import YOLO

def main():
    print("==========================================================")
    print(" Експортиране на YOLO модел за Raspberry Pi 5 (ARM)       ")
    print("==========================================================")
    
    # 1. Зареди оригиналния PyTorch модел
    model_path = "../models/newmodel.pt"
    print(f"Зареждане на {model_path} ...")
    model = YOLO(model_path)
    
    # 2. Експортиране в NCNN формат.
    # NCNN е специално създаден от Tencent за ARM архитектури (Raspberry Pi/Smartphones).
    # Използва Vulkan и NEON инструкции за максимален FPS без външно GPU.
    try:
        print("Конвертиране в NCNN формат (може да отнеме минута)...")
        model.export(format="ncnn", half=True)
        print("\n✅ УСПЕХ! Моделът е оптимизиран за Raspberry Pi.")
        print("----------------------------------------------------------")
        print("Сега отвори 'config.yaml' и промени 'yolo_model' на:")
        print("yolo_model: 'models/newmodel_ncnn_model'")
        print("----------------------------------------------------------")
    except Exception as e:
        print(f"Грешка при експорта: {e}")
        
if __name__ == "__main__":
    main()
