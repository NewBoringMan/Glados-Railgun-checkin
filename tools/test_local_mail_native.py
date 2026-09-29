"""Compile the native Mail adapter and UI without opening Mail or reading mail."""
from __future__ import annotations
import json
from pathlib import Path
import platform
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]

def compile_reader(output:Path):
    directory=output.parent
    definition=Path('/System/Applications/Mail.app/Contents/Resources/Mail.sdef')
    if not definition.is_file():raise RuntimeError('installed_mail_dictionary_missing')
    p=subprocess.run(['/usr/bin/sdp','-fh','--basename','Mail'],input=definition.read_bytes(),
                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=directory,timeout=20)
    if p.returncode:raise RuntimeError(p.stderr.decode()[-3000:])
    result=subprocess.run(['/usr/bin/xcrun','clang','-fobjc-arc','-fmodules',
                           '-fmodules-cache-path='+str(directory/'ModuleCache'),
                           '-I',str(directory),str(ROOT/'app_integration/LocalMailReader.m'),
                           '-framework','Foundation','-framework','AppKit','-framework','Carbon',
                           '-framework','ScriptingBridge','-o',str(output)],
                          capture_output=True,text=True,timeout=120)
    if result.returncode:raise RuntimeError(result.stderr[-7000:])

def main():
    if platform.system()!='Darwin':
        print(json.dumps({'status':'NOT_RUN','reason':'macOS_required'}));return 2
    with tempfile.TemporaryDirectory(prefix='glados-mail-native-') as tmp:
        output=Path(tmp)/'LocalMailReader';compile_reader(output)
        result=subprocess.run([str(output),'--self-test'],capture_output=True,text=True,timeout=10)
        data=json.loads(result.stdout)
        if result.returncode or data.get('ok') is not True or data.get('mail_contacted') is not False:
            raise RuntimeError('synthetic_native_test_failed')
        native_ui=subprocess.run(['/usr/bin/xcrun','swiftc','-parse-as-library','-emit-library',
                                  '-module-cache-path',str(Path(tmp)/'SwiftModuleCache'),
                                  '-target','arm64-apple-macos13.0','-framework','SwiftUI',
                                  '-framework','AppKit','-framework','UserNotifications',
                                  str(ROOT/'app_integration/RefreshCenter.swift'),
                                  '-o',str(Path(tmp)/'RefreshCenter.dylib')],
                                 capture_output=True,text=True,timeout=120)
        if native_ui.returncode:raise RuntimeError(native_ui.stderr[-7000:])
        print(json.dumps({'status':'PASS','native_ui_compiled':True,'compiled_from_installed_mail_dictionary':True,
                          'synthetic_self_test':True,'mail_contacted':False,'ui_requested':False}))
    print(json.dumps({'temporary_build_removed':not Path(tmp).exists()}));return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'status':'FAIL','reason':str(exc)}));raise SystemExit(1)
