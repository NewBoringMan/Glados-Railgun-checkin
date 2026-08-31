#import <AppKit/AppKit.h>
#import <dlfcn.h>

@interface GLaDOSPolicyMenuTarget : NSObject
- (void)openPolicyEditor:(id)sender;
@end

static void *policyEditorHandle = NULL;

typedef void (*GLaDOSShowPolicyEditorFunction)(void);

@implementation GLaDOSPolicyMenuTarget
- (void)openPolicyEditor:(id)sender {
    NSString *bundlePath = [[NSBundle mainBundle] bundlePath];
    NSString *libraryPath = [bundlePath stringByAppendingPathComponent:@"Contents/Frameworks/GLaDOSPolicyEditor.dylib"];

    if (![[NSFileManager defaultManager] fileExistsAtPath:libraryPath]) {
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"账号兑换方案模块不可用";
        alert.informativeText = @"未找到 Account Center 内置的兑换方案模块。请重新安装 GLaDOS Account Center。";
        [alert runModal];
        return;
    }

    if (!policyEditorHandle) {
        policyEditorHandle = dlopen(libraryPath.fileSystemRepresentation, RTLD_NOW | RTLD_LOCAL);
    }
    if (!policyEditorHandle) {
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"无法加载账号兑换方案模块";
        const char *message = dlerror();
        alert.informativeText = message ? [NSString stringWithUTF8String:message] : @"未知错误";
        [alert runModal];
        return;
    }

    dlerror();
    GLaDOSShowPolicyEditorFunction showEditor = (GLaDOSShowPolicyEditorFunction)dlsym(policyEditorHandle, "GLaDOSShowPolicyEditor");
    const char *symbolError = dlerror();
    if (!showEditor || symbolError) {
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"账号兑换方案模块版本不兼容";
        alert.informativeText = symbolError ? [NSString stringWithUTF8String:symbolError] : @"未找到窗口入口。";
        [alert runModal];
        return;
    }

    showEditor();
}
@end

static GLaDOSPolicyMenuTarget *policyMenuTarget = nil;

static void InstallPolicyMenu(void) {
    if (!NSApp || !NSApp.mainMenu || policyMenuTarget) {
        return;
    }

    NSMenuItem *appMenuItem = [NSApp.mainMenu itemAtIndex:0];
    NSMenu *appMenu = appMenuItem.submenu;
    if (!appMenu) {
        return;
    }

    for (NSMenuItem *item in appMenu.itemArray) {
        if ([item.title isEqualToString:@"账号兑换方案…"]) {
            return;
        }
    }

    policyMenuTarget = [[GLaDOSPolicyMenuTarget alloc] init];
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:@"账号兑换方案…"
                                                 action:@selector(openPolicyEditor:)
                                          keyEquivalent:@","];
    item.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagOption;
    item.target = policyMenuTarget;

    NSInteger insertionIndex = MIN((NSInteger)appMenu.numberOfItems, 2);
    [appMenu insertItem:item atIndex:insertionIndex];
}

__attribute__((constructor))
static void GLaDOSPolicyPluginInitialize(void) {
    dispatch_async(dispatch_get_main_queue(), ^{
        [[NSNotificationCenter defaultCenter] addObserverForName:NSApplicationDidFinishLaunchingNotification
                                                          object:nil
                                                           queue:[NSOperationQueue mainQueue]
                                                      usingBlock:^(__unused NSNotification *note) {
            InstallPolicyMenu();
        }];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.0 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            InstallPolicyMenu();
        });
    });
}
