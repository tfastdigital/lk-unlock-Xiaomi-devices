
# lk-unlock

**lk-unlock** is a lightweight Python tool for unlocking the bootloader of Xiaomi devices with MediaTek (MTK) SoCs by patching the little kernel (LK) image.

This tool replaces Xiaomi's embedded public key with a user-controlled key and applies a cert bypass to the LK image, enabling offline unlock signature generation.

> **⚠️ WARNING:** This method is dangerous and may brick your device. Use it only if you know how to recover your device and accept the risk.

---

## About this Solution

Solution by **Tfast Digital Agency**.

Website: https://tfastdigital.com

GitHub: https://github.com/tfastdigital/lk-unlock-Xiaomi-devices

---

## How it Works

Xiaomi devices protect bootloader unlock using RSA signatures. The device requests a one-time token, the Xiaomi server signs it with its private key, and the LK bootloader verifies the returned signature using the public key embedded in the image.

This tool leverages a vulnerability in MTK LK images that allows patching the bootloader and bypassing certificate verification. By replacing Xiaomi's public key with your own and applying a cert bypass, the device will accept signatures generated locally with the matched private key.

## Requirements

- Python 3.7 or newer
- `fastboot` available in `PATH` or the current working directory
- Python dependencies:
  ```bash
  pip install cryptography
  pip install git+https://github.com/R0rt1z2/liblk
  ```

## Installation

1. Clone or download this repository.
2. Install the required Python dependencies.
3. Make sure `fastboot` is installed and accessible.

## Usage

The script supports three commands:

- `patch` — patch the LK image and apply cert bypass
- `sign` — sign a fastboot unlock token locally
- `unlock` — run the unlock sequence against a fastboot device

### 1. Patch the LK image

Obtain your device's `lk.img` first (from firmware or by reading it in BROM mode):

```bash
python lk-unlock.py patch lk.img -o lk_patched.img
```

Options:
- `--wrap` — use wrap mode for cert bypass (default mode is `override`)

What this does:
- Generates `private.pem` and `public.pem` if missing
- Replaces Xiaomi's public key modulus inside `lk.img`
- Applies the cert bypass to signed partitions
- Writes the patched image to `lk_patched.img`

### 2. Flash the patched LK image

Use one of these methods to flash the patched image onto the device:

- MTKClient (if supported):
  ```bash
  python mtk.py r lk_a,lk_b lk_a_backup.img,lk_b_backup.img
  python mtk.py w lk_a,lk_b lk_patched.img,lk_patched.img
  ```
- Xiaomi BROM auth service
- Temporary root exploit (Ghostlock, etc.)
- UFS programmer or hardware tool

### 3. Unlock the device

After flashing the patched LK and rebooting to fastboot:

```bash
python lk-unlock.py unlock
```

This will:
- Detect the fastboot device
- Request the unlock token
- Sign the token with `private.pem`
- Upload `signature.bin`
- Send the unlock command

Options:
- `--dry-run` — sign the token without staging or unlocking the device

### 4. Manual token signing

To sign a token manually:

```bash
fastboot oem get_token
python lk-unlock.py sign "TOKEN"
fastboot stage signature.bin
fastboot oem unlock
```

## Files Generated

- `private.pem` — RSA private key used for signing
- `public.pem` — RSA public key embedded into the patched LK image
- `signature.bin` — generated unlock signature for fastboot

## Disclaimer

This tool is provided for research and educational purposes. Tfast Digital Agency is not responsible for damage caused by misuse. Use at your own risk.

