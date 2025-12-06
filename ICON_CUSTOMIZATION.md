# MFT System - Custom Icon Guide

## Using Your Custom Icon

The MFT System installer supports custom desktop icons. Follow these steps to use your own icon:

### Option 1: Quick Method (Before Installation)

1. **Prepare your icon:**
   - Use your icon file (should be `.ico` format)
   - Rename it to `mft_icon.ico`
   - Place it in the same folder as `install.bat`

2. **Run the installer:**
   - The installer will automatically copy your icon
   - The desktop shortcut will use your custom icon

### Option 2: Create Icon from Image

If you have a PNG/JPG image instead of an ICO file:

1. **Using the built-in icon creator:**
   ```powershell
   # Install Pillow if not already installed
   pip install Pillow

   # Run the icon creator (edit the script to use your image)
   python create_icon.py
   ```

2. **Or use an online converter:**
   - Visit: https://convertio.co/png-ico/
   - Upload your image (256x256 pixels recommended)
   - Download the `.ico` file
   - Rename to `mft_icon.ico`

### Option 3: Add Icon After Installation

If you already installed the system:

1. **Copy your icon:**
   ```batch
   copy mft_icon.ico "C:\Program Files\MFT-System\"
   ```

2. **Update the desktop shortcut:**
   ```powershell
   $shell = New-Object -COM WScript.Shell
   $shortcut = $shell.CreateShortcut("$env:PUBLIC\Desktop\MFT System.lnk")
   $shortcut.IconLocation = "C:\Program Files\MFT-System\mft_icon.ico"
   $shortcut.Save()
   ```

### Icon Requirements

- **Format:** `.ico` (Windows Icon)
- **Recommended Size:** 256x256 pixels
- **Supported Formats (for conversion):**
  - PNG, JPG, BMP, GIF
  - SVG (needs conversion tool)

### Sample Icon Specifications

Based on your icon style (if you have a specific design):

1. **Export from design tool:**
   - Resolution: 256x256 or higher
   - Format: PNG with transparency

2. **Convert to ICO:**
   - Use online tool or Pillow
   - Include multiple sizes: 16x16, 32x32, 48x48, 64x64, 128x128, 256x256

3. **Place in installation folder**

### Troubleshooting

**Icon not showing on desktop:**
- Refresh desktop (F5)
- Rebuild icon cache: `ie4uinit.exe -show`
- Check icon file path is correct

**Wrong icon appearing:**
- Clear Windows icon cache:
  ```batch
  taskkill /f /im explorer.exe
  del /a /q "%localappdata%\IconCache.db"
  start explorer.exe
  ```

## Custom Icon Creation Script

The `create_icon.py` script can be customized to generate an icon with your preferred:
- Colors
- Text/Logo
- Background style

Edit the script and modify:
- `fill='#3498db'` - Background color
- `text = "MFT"` - Display text
- Font and styling options

Then run: `python create_icon.py`
