from PIL import Image, ImageDraw

img = Image.new("RGB", (1200, 900), "white")
draw = ImageDraw.Draw(img)

questions = [
    ("1-savol: O'zbekiston poytaxti qaysi?", 50),
    ("2-savol: 2 + 2 nechaga teng?", 300),
    ("3-savol: Yerning tabiiy yo'ldoshi nima?", 550),
]

for text, y in questions:
    draw.rectangle((40, y, 1160, y + 180), outline="black", width=3)
    draw.text((70, y + 30), text, fill="black")
    draw.text((70, y + 80), "A) Variant A", fill="black")
    draw.text((70, y + 110), "B) Variant B", fill="black")
    draw.text((500, y + 80), "C) Variant C", fill="black")
    draw.text((500, y + 110), "D) Variant D", fill="black")

img.save("test_big_image.jpg", quality=95)
print("TEST IMAGE CREATED: test_big_image.jpg")
