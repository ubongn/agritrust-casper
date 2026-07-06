"""Download and extract binaryen (wasm-opt) for Windows."""
import urllib.request
import tarfile
import os

# Binaryen releases use .tar.gz even for Windows
url = 'https://github.com/WebAssembly/binaryen/releases/download/version_120/binaryen-version_120-x86_64-windows.tar.gz'
dest = r'C:\Users\Sabiedu\binaryen.tar.gz'

print(f'Downloading from {url}...')
urllib.request.urlretrieve(url, dest)
print(f'Downloaded to {dest} ({os.path.getsize(dest):,} bytes)')

print('Extracting...')
with tarfile.open(dest, 'r:gz') as tar:
    tar.extractall('C:/Users/Sabiedu/')

# Find wasm-opt
for root, dirs, files in os.walk(r'C:\Users\Sabiedu\binaryen-version_120-x86_64-windows'):
    for f in files:
        if 'wasm-opt' in f:
            path = os.path.join(root, f)
            print(f'Found: {path}')
            # Copy to a PATH location
            import shutil
            target = r'C:\Users\Sabiedu\.cargo\bin\wasm-opt.exe'
            shutil.copy2(path, target)
            print(f'Copied to: {target}')
            break

print('Done!')
