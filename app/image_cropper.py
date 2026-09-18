from pathlib import Path
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import messagebox


class ImageCropper:
    def __init__(self, image_path: str, output_dir: str, count: int):
        self.image_path = Path(image_path)
        self.output_dir = Path(output_dir)
        self.count = count

        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.image = Image.open(self.image_path).convert("RGB")
        self.original_width, self.original_height = self.image.size

        self.root = tk.Tk()
        self.root.title("Doniyor Academy — Savollarni belgilash")

        self.max_width = 1200
        self.max_height = 800

        scale_x = self.max_width / self.original_width
        scale_y = self.max_height / self.original_height
        self.scale = min(1.0, scale_x, scale_y)

        display_width = int(self.original_width * self.scale)
        display_height = int(self.original_height * self.scale)

        self.display_image = self.image.resize(
            (display_width, display_height),
            Image.Resampling.LANCZOS,
        )

        self.photo = ImageTk.PhotoImage(self.display_image)

        self.canvas = tk.Canvas(
            self.root,
            width=display_width,
            height=display_height,
            cursor="crosshair",
        )
        self.canvas.pack(padx=10, pady=10)

        self.canvas.create_image(
            0,
            0,
            anchor="nw",
            image=self.photo,
        )

        self.info = tk.Label(
            self.root,
            text=f"1-savolni belgilang. Jami: {self.count}",
            font=("Arial", 12),
        )
        self.info.pack(pady=(0, 10))

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

        self.start_x = None
        self.start_y = None
        self.rectangle = None
        self.current_index = 1
        self.saved_paths = []

        self.root.protocol("WM_DELETE_WINDOW", self.cancel)

    def on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y

        if self.rectangle is not None:
            self.canvas.delete(self.rectangle)

        self.rectangle = self.canvas.create_rectangle(
            self.start_x,
            self.start_y,
            self.start_x,
            self.start_y,
            outline="red",
            width=3,
        )

    def on_drag(self, event):
        if self.rectangle is None:
            return

        self.canvas.coords(
            self.rectangle,
            self.start_x,
            self.start_y,
            event.x,
            event.y,
        )

    def on_release(self, event):
        if self.start_x is None or self.start_y is None:
            return

        x1 = min(self.start_x, event.x)
        y1 = min(self.start_y, event.y)
        x2 = max(self.start_x, event.x)
        y2 = max(self.start_y, event.y)

        if x2 - x1 < 10 or y2 - y1 < 10:
            return

        original_x1 = max(0, int(x1 / self.scale))
        original_y1 = max(0, int(y1 / self.scale))
        original_x2 = min(self.original_width, int(x2 / self.scale))
        original_y2 = min(self.original_height, int(y2 / self.scale))

        if original_x2 <= original_x1 or original_y2 <= original_y1:
            return

        cropped = self.image.crop(
            (
                original_x1,
                original_y1,
                original_x2,
                original_y2,
            )
        )

        output_path = (
            self.output_dir
            / f"crop_{self.current_index}.jpg"
        )

        cropped.save(
            output_path,
            "JPEG",
            quality=95,
        )

        self.saved_paths.append(str(output_path))

        if self.current_index >= self.count:
            self.info.config(
                text="Barcha savollar belgilandi. Oynani yopishingiz mumkin."
            )
            messagebox.showinfo(
                "Tayyor",
                f"{self.count} ta savol rasmi tayyor bo'ldi.",
            )
            self.root.destroy()
            return

        self.current_index += 1
        self.start_x = None
        self.start_y = None

        if self.rectangle is not None:
            self.canvas.delete(self.rectangle)
            self.rectangle = None

        self.info.config(
            text=(
                f"{self.current_index}-savolni belgilang. "
                f"Jami: {self.count}"
            )
        )

    def cancel(self):
        self.saved_paths = []
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return self.saved_paths


def crop_questions(
    image_path: str,
    output_dir: str,
    count: int,
):
    cropper = ImageCropper(
        image_path=image_path,
        output_dir=output_dir,
        count=count,
    )
    return cropper.run()

if __name__ == "__main__":
    import sys

    if len(sys.argv) != 4:
        print("USAGE: python image_cropper.py IMAGE_PATH OUTPUT_DIR COUNT")
        raise SystemExit(1)

    image_path = sys.argv[1]
    output_dir = sys.argv[2]
    count = int(sys.argv[3])

    result = crop_questions(
        image_path=image_path,
        output_dir=output_dir,
        count=count,
    )

    print("CROPS_READY")
    for path in result:
        print(path)
