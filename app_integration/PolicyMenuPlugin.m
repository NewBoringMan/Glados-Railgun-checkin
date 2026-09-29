#import <AppKit/AppKit.h>
#import <dlfcn.h>

@interface GLaDOSPolicyMenuTarget : NSObject
- (void)openPolicyEditor:(id)sender;
- (void)openRefreshCenter:(id)sender;
@end

static void *policyEditorHandle = NULL;
static void *refreshCenterHandle = NULL;
static NSString *const PolicyButtonIdentifier = @"com.enoch.glados-account-center.policy-button";

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

- (void)openRefreshCenter:(id)sender {
    NSString *path = [[[NSBundle mainBundle] bundlePath] stringByAppendingPathComponent:@"Contents/Frameworks/GLaDOSRefreshCenter.dylib"];
    if (!refreshCenterHandle) {
        refreshCenterHandle = dlopen(path.fileSystemRepresentation, RTLD_NOW | RTLD_LOCAL);
    }
    void (*showRefresh)(void) = refreshCenterHandle ? (void (*)(void))dlsym(refreshCenterHandle, "GLaDOSShowRefreshCenter") : NULL;
    if (!showRefresh) {
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"登录维护模块不可用";
        alert.informativeText = @"请检查 Account Center 安装完整性。原账号、签到与兑换设置未被改动。";
        [alert runModal];
        return;
    }
    showRefresh();
}
@end

static GLaDOSPolicyMenuTarget *policyMenuTarget = nil;

static BOOL IsMainAccountCenterWindow(NSWindow *window) {
    if (!window || !(window.styleMask & NSWindowStyleMaskTitled)) {
        return NO;
    }

    static NSSet<NSString *> *titles = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        titles = [NSSet setWithArray:@[@"概览", @"账号", @"签到日历", @"运行记录", @"积分方案", @"设置"]];
    });
    return [titles containsObject:window.title ?: @""];
}

static void InstallPolicyButtonOnWindow(NSWindow *window) {
    if (!policyMenuTarget || !IsMainAccountCenterWindow(window)) {
        return;
    }

    for (NSTitlebarAccessoryViewController *controller in window.titlebarAccessoryViewControllers) {
        if ([controller.view.identifier isEqualToString:PolicyButtonIdentifier]) {
            return;
        }
    }

    NSButton *button = [NSButton buttonWithTitle:@"兑换方案"
                                          target:policyMenuTarget
                                          action:@selector(openPolicyEditor:)];
    button.bezelStyle = NSBezelStyleTexturedRounded;
    button.controlSize = NSControlSizeRegular;
    button.toolTip = @"调整每个 GLaDOS 账号使用的兑换方案";
    [button sizeToFit];

    NSButton *refreshButton = [NSButton buttonWithTitle:@"登录维护" target:policyMenuTarget action:@selector(openRefreshCenter:)];
    refreshButton.bezelStyle = NSBezelStyleTexturedRounded;
    refreshButton.controlSize = NSControlSizeRegular;
    refreshButton.toolTip = @"保存账号邮箱、配置收码授权并检查登录维护状态";
    [refreshButton sizeToFit];
    NSRect buttonFrame = button.frame;
    NSRect refreshFrame = refreshButton.frame;
    NSView *container = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, buttonFrame.size.width + refreshFrame.size.width + 20.0, 28.0)];
    container.identifier = PolicyButtonIdentifier;
    button.frame = NSMakeRect(6.0,
                              floor((container.bounds.size.height - buttonFrame.size.height) / 2.0),
                              buttonFrame.size.width,
                              buttonFrame.size.height);
    [container addSubview:button];
    refreshButton.frame = NSMakeRect(NSMaxX(button.frame) + 8.0,
                                    floor((container.bounds.size.height - refreshFrame.size.height) / 2.0),
                                    refreshFrame.size.width, refreshFrame.size.height);
    [container addSubview:refreshButton];

    NSTitlebarAccessoryViewController *accessory = [[NSTitlebarAccessoryViewController alloc] init];
    accessory.view = container;
    accessory.layoutAttribute = NSLayoutAttributeRight;
    [window addTitlebarAccessoryViewController:accessory];
}

static void InstallPolicyButtonOnAllWindows(void) {
    for (NSWindow *window in NSApp.windows) {
        InstallPolicyButtonOnWindow(window);
    }
}

static void InstallPolicyMenu(void) {
    if (!NSApp || !NSApp.mainMenu) {
        return;
    }

    NSMenuItem *appMenuItem = [NSApp.mainMenu itemAtIndex:0];
    NSMenu *appMenu = appMenuItem.submenu;
    if (!appMenu) {
        return;
    }

    if (!policyMenuTarget) {
        policyMenuTarget = [[GLaDOSPolicyMenuTarget alloc] init];
    }

    BOOL hasPolicyMenuItem = NO;
    for (NSMenuItem *item in appMenu.itemArray) {
        if ([item.title isEqualToString:@"账号兑换方案…"]) {
            hasPolicyMenuItem = YES;
            break;
        }
    }

    if (!hasPolicyMenuItem) {
        NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:@"账号兑换方案…"
                                                     action:@selector(openPolicyEditor:)
                                              keyEquivalent:@","];
        item.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagOption;
        item.target = policyMenuTarget;

        NSInteger insertionIndex = MIN((NSInteger)appMenu.numberOfItems, 2);
        [appMenu insertItem:item atIndex:insertionIndex];
    }

    BOOL hasRefreshMenuItem = NO;
    for (NSMenuItem *item in appMenu.itemArray) {
        if ([item.title isEqualToString:@"登录维护…"]) { hasRefreshMenuItem = YES; break; }
    }
    if (!hasRefreshMenuItem) {
        NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:@"登录维护…" action:@selector(openRefreshCenter:) keyEquivalent:@"l"];
        item.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagOption;
        item.target = policyMenuTarget;
        [appMenu insertItem:item atIndex:MIN((NSInteger)appMenu.numberOfItems, 3)];
    }
    InstallPolicyButtonOnAllWindows();
}

__attribute__((constructor))
static void GLaDOSPolicyPluginInitialize(void) {
    dispatch_async(dispatch_get_main_queue(), ^{
        [[NSNotificationCenter defaultCenter] addObserverForName:NSApplicationDidFinishLaunchingNotification
                                                          object:nil
                                                           queue:[NSOperationQueue mainQueue]
                                                      usingBlock:^(__unused NSNotification *note) {
            InstallPolicyMenu();
            InstallPolicyButtonOnAllWindows();
        }];
        [[NSNotificationCenter defaultCenter] addObserverForName:NSWindowDidBecomeMainNotification
                                                          object:nil
                                                           queue:[NSOperationQueue mainQueue]
                                                      usingBlock:^(NSNotification *note) {
            if ([note.object isKindOfClass:[NSWindow class]]) {
                InstallPolicyButtonOnWindow((NSWindow *)note.object);
            }
        }];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.0 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            InstallPolicyMenu();
            InstallPolicyButtonOnAllWindows();
        });
    });
}
