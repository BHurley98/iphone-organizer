import os,sys,shutil,json,importlib.metadata,argparse
from pathlib import Path
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--python-source',required=True);parser.add_argument('--packages-source',required=True);args=parser.parse_args();source=Path(args.python_source).resolve()
packages=Path(args.packages_source).resolve()
sys.path[:0]=[str(packages),str(packages/'win32'),str(packages/'win32/lib')]
dll=os.add_dll_directory(str(packages/'pywin32_system32'))
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.installation_proxy import InstallationProxyService
from pymobiledevice3.services.springboard import SpringBoardServicesService
from pymobiledevice3.usbmux import list_devices
# Copy the signed bundled interpreter and its standard library, excluding host packages.
runtime=root/'runtime';runtime.mkdir(exist_ok=True)
for file in source.iterdir():
    if file.is_file():shutil.copy2(file,runtime/file.name)
for name in ['Lib','DLLs']:
    shutil.copytree(source/name,runtime/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('site-packages','__pycache__','test','tests'))
# Only ship packages imported by the USB/app/layout stack, plus lazy runtime dependencies.
names=set()
for module in list(sys.modules.values()):
    filename=getattr(module,'__file__',None)
    if filename:
        try:names.add(Path(filename).resolve().relative_to(packages).parts[0])
        except ValueError:pass
names.update(['pymobiledevice3','pywin32_system32','win32','win32com','win32comext','pythonwin','certifi','sslpsk_pmd3','sslpsk','psutil','qh3','qh3.libs','ifaddr','zeroconf','srptools','packaging','typing_extensions.py'])
target=root/'packages';target.mkdir(exist_ok=True)
for name in sorted(names):
    item=packages/name
    if not item.exists():continue
    if item.is_dir():shutil.copytree(item,target/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','tests','test'))
    else:shutil.copy2(item,target/name)
    libs=packages/(name+'.libs')
    if libs.is_dir():shutil.copytree(libs,target/libs.name,dirs_exist_ok=True)
selected_roots={n.split('.')[0] for n in names}
distributions=[]
for distribution in importlib.metadata.distributions(path=[str(packages)]):
    files=distribution.files or []
    roots={str(f).split('/')[0].split('.')[0] for f in files}
    if roots & selected_roots:
        metadata=Path(distribution._path)
        shutil.copytree(metadata,target/metadata.name,dirs_exist_ok=True)
        distributions.append({'name':distribution.metadata['Name'],'version':distribution.version})
(root/'THIRD-PARTY-PACKAGES.json').write_text(json.dumps(distributions,indent=2))
print(json.dumps({'package_roots':sorted(names),'distributions':len(distributions),'runtime_mb':round(sum(f.stat().st_size for f in runtime.rglob('*') if f.is_file())/2**20,1),'packages_mb':round(sum(f.stat().st_size for f in target.rglob('*') if f.is_file())/2**20,1)}))

