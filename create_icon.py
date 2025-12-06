"""
Create a simple MFT System icon
This creates a basic icon file that can be customized later
"""

try:
    from PIL import Image, ImageDraw, ImageFont
    import os

    def create_mft_icon():
        """Create a simple MFT icon"""
        # Create a 256x256 image with a gradient background
        size = 256
        img = Image.new('RGB', (size, size), '#2c3e50')
        draw = ImageDraw.Draw(img)

        # Draw a rounded rectangle background
        margin = 20
        draw.rounded_rectangle(
            [margin, margin, size-margin, size-margin],
            radius=30,
            fill='#3498db',
            outline='#2980b9',
            width=3
        )

        # Draw "MFT" text
        try:
            # Try to use a nice font
            font = ImageFont.truetype("arial.ttf", 80)
        except:
            # Fallback to default font
            font = ImageFont.load_default()

        text = "MFT"
        # Get text bounding box for centering
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        text_x = (size - text_width) // 2
        text_y = (size - text_height) // 2 - 10

        # Draw text with shadow
        draw.text((text_x + 3, text_y + 3), text, fill='#000000', font=font)
        draw.text((text_x, text_y), text, fill='#ffffff', font=font)

        # Save as ICO file with multiple sizes
        icon_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
        img.save('mft_icon.ico', format='ICO', sizes=icon_sizes)
        print("✅ Icon created: mft_icon.ico")

        # Also save as PNG for reference
        img.save('mft_icon.png', format='PNG')
        print("✅ Preview created: mft_icon.png")

        return True

    if __name__ == '__main__':
        if create_mft_icon():
            print("\n📝 To use a custom icon:")
            print("   1. Replace mft_icon.ico with your custom icon")
            print("   2. Re-run install.bat")
            print("   3. The desktop shortcut will use your custom icon")

except ImportError:
    print("⚠️  PIL/Pillow not installed")
    print("\nTo create a custom icon:")
    print("1. Install Pillow: pip install Pillow")
    print("2. Run this script again")
    print("\nOr manually create an icon:")
    print("1. Create a 256x256 PNG image")
    print("2. Convert to ICO using an online tool or image editor")
    print("3. Save as: mft_icon.ico")
