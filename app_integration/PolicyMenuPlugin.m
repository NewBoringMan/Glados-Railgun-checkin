#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#import <dlfcn.h>
#import <objc/runtime.h>

@interface GLaDOSPolicyMenuTarget : NSObject
- (void)openPolicyEditor:(id)sender;
@end

@interface GLaDOSAccountSessionTarget : NSObject
- (void)openAccountGlados:(NSMenuItem *)sender;
@end

static void *policyEditorHandle = NULL;
static NSString *const PolicyButtonIdentifier = @"com.enoch.glados-account-center.policy-button";
static NSString *const AccountMenuOpenTitle = @"打开 GLaDOS";
static const void *AccountKeyAssociation = &AccountKeyAssociation;

typedef void (*GLaDOSShowPolicyEditorFunction)(void);

static NSString *SupportDirectory(void) {
    return [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/GLaDOS Account Center"];
}

static NSString *AccountProfilesRoot(void) {
    return [SupportDirectory() stringByAppendingPathComponent:@"BrowserProfiles/accounts"];
}

static BOOL IsValidAccountKey(NSString *value) {
    if (![value isKindOfClass:[NSString class]]) return NO;
    NSString *key = [value uppercaseString];
    if (key.length != 16) return NO;
    NSCharacterSet *invalid = [[NSCharacterSet characterSetWithCharactersInString:@"0123456789ABCDEF"] invertedSet];
    return [key rangeOfCharacterFromSet:invalid].location == NSNotFound;
}

static NSString *StringFromAccessibilityValue(id value) {
    if ([value isKindOfClass:[NSString class]]) return value;
    if ([value isKindOfClass:[NSAttributedString class]]) return [(NSAttributedString *)value string];
    return nil;
}

static void CollectAccountKeyElements(id element,
                                      NSMutableArray<NSDictionary *> *results,
                                      NSHashTable *visited,
                                      NSUInteger depth) {
    if (!element || depth > 24 || results.count > 256 || [visited containsObject:element]) return;
    [visited addObject:element];

    @try {
        NSString *text = nil;
        if ([element respondsToSelector:@selector(accessibilityValue)]) {
            text = StringFromAccessibilityValue([element accessibilityValue]);
        }
        if (!text.length && [element respondsToSelector:@selector(accessibilityTitle)]) {
            text = StringFromAccessibilityValue([element accessibilityTitle]);
        }
        text = [text stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        if (IsValidAccountKey(text) && [element respondsToSelector:@selector(accessibilityFrame)]) {
            NSRect frame = [element accessibilityFrame];
            if (!NSIsEmptyRect(frame)) {
                [results addObject:@{@"key": [text uppercaseString], @"frame": [NSValue valueWithRect:frame]}];
            }
        }

        if ([element respondsToSelector:@selector(accessibilityChildren)]) {
            NSArray *children = [element accessibilityChildren];
            if ([children isKindOfClass:[NSArray class]]) {
                for (id child in children) {
                    CollectAccountKeyElements(child, results, visited, depth + 1);
                }
            }
        }
    } @catch (__unused NSException *exception) {
        return;
    }
}

static CGFloat VerticalDistanceToMouse(NSRect frame, NSPoint mouse) {
    CGFloat middleY = NSMidY(frame);
    CGFloat best = fabs(middleY - mouse.y);
    for (NSScreen *screen in NSScreen.screens) {
        if (NSPointInRect(mouse, screen.frame)) {
            CGFloat mirroredY = NSMaxY(screen.frame) - (mouse.y - NSMinY(screen.frame));
            best = MIN(best, fabs(middleY - mirroredY));
            break;
        }
    }
    return best;
}

static NSString *AccountKeyNearCurrentMenu(void) {
    NSWindow *accountWindow = nil;
    for (NSWindow *window in NSApp.windows) {
        if ([window.title isEqualToString:@"账号"] && window.isVisible) {
            accountWindow = window;
            break;
        }
    }
    if (!accountWindow) accountWindow = NSApp.mainWindow;
    if (!accountWindow) return nil;

    NSMutableArray<NSDictionary *> *candidates = [NSMutableArray array];
    NSHashTable *visited = [NSHashTable hashTableWithOptions:NSPointerFunctionsObjectPointerPersonality];
    CollectAccountKeyElements(accountWindow.contentView ?: accountWindow, candidates, visited, 0);
    if (candidates.count == 0) return nil;

    NSPoint mouse = [NSEvent mouseLocation];
    NSString *bestKey = nil;
    CGFloat bestDistance = CGFLOAT_MAX;
    for (NSDictionary *candidate in candidates) {
        NSRect frame = [candidate[@"frame"] rectValue];
        CGFloat distance = VerticalDistanceToMouse(frame, mouse);
        if (distance < bestDistance) {
            bestDistance = distance;
            bestKey = candidate[@"key"];
        }
    }

    // If the pointer is nowhere near a visible account card, fail closed instead of
    // falling back to a generic browser session that could be logged into another account.
    return bestDistance <= 360.0 ? bestKey : nil;
}

static NSString *EmailForAccountKey(NSString *accountKey) {
    NSString *cachePath = [SupportDirectory() stringByAppendingPathComponent:@"status-cache.json"];
    NSData *data = [NSData dataWithContentsOfFile:cachePath];
    if (!data) return nil;
    id json = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    if (![json isKindOfClass:[NSArray class]]) return nil;
    for (NSDictionary *entry in (NSArray *)json) {
        if (![entry isKindOfClass:[NSDictionary class]]) continue;
        NSString *key = [entry[@"account_key"] isKindOfClass:[NSString class]] ? [entry[@"account_key"] uppercaseString] : @"";
        if (![key isEqualToString:[accountKey uppercaseString]]) continue;
        NSString *email = [entry[@"email"] isKindOfClass:[NSString class]] ? entry[@"email"] : @"";
        email = [email stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        return email.length ? email : nil;
    }
    return nil;
}

static void ShowSessionAlert(NSString *title, NSString *message) {
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = title ?: @"GLaDOS Account Center";
    alert.informativeText = message ?: @"";
    [alert runModal];
}

static BOOL EdgeInstalled(void) {
    NSArray<NSString *> *paths = @[
        @"/Applications/Microsoft Edge.app",
        [NSHomeDirectory() stringByAppendingPathComponent:@"Applications/Microsoft Edge.app"]
    ];
    for (NSString *path in paths) {
        if ([[NSFileManager defaultManager] fileExistsAtPath:path]) return YES;
    }
    return NO;
}

static BOOL LaunchAccountEdgeProfile(NSString *accountKey) {
    if (!IsValidAccountKey(accountKey)) {
        ShowSessionAlert(@"无法识别账号",
                         @"没有可靠识别到当前账号的 Account Key。请重新打开该账号右侧菜单后再试。\n\n为避免串号，本次不会打开一个不确定的 GLaDOS 会话。");
        return NO;
    }
    if (!EdgeInstalled()) {
        ShowSessionAlert(@"没有找到 Microsoft Edge",
                         @"账号专属网页登录使用 Microsoft Edge 的独立浏览器资料。请先安装 Microsoft Edge 后再试。");
        return NO;
    }

    NSString *profilePath = [[[AccountProfilesRoot() stringByAppendingPathComponent:[accountKey uppercaseString]]
                              stringByAppendingPathComponent:@"edge"] stringByStandardizingPath];
    BOOL firstUse = ![[NSFileManager defaultManager] fileExistsAtPath:profilePath];
    NSError *directoryError = nil;
    if (![[NSFileManager defaultManager] createDirectoryAtPath:profilePath
                                   withIntermediateDirectories:YES
                                                    attributes:@{NSFilePosixPermissions: @0700}
                                                         error:&directoryError]) {
        ShowSessionAlert(@"无法创建账号专属浏览器资料", directoryError.localizedDescription ?: @"未知错误");
        return NO;
    }

    if (firstUse) {
        NSString *email = EmailForAccountKey(accountKey);
        NSString *name = email.length ? email : [accountKey uppercaseString];
        ShowSessionAlert(@"首次建立账号专属登录会话",
                         [NSString stringWithFormat:@"将为 %@ 创建独立的 Microsoft Edge 浏览器资料。\n\n第一次请在随后打开的 GLaDOS 页面正常登录一次这个账号；以后从该账号的“打开 GLaDOS”进入时，会直接复用这一份登录状态，不会与其他账号混用。", name]);
    }

    NSTask *task = [[NSTask alloc] init];
    task.executableURL = [NSURL fileURLWithPath:@"/usr/bin/open"];
    task.arguments = @[
        @"-na", @"Microsoft Edge", @"--args",
        [NSString stringWithFormat:@"--user-data-dir=%@", profilePath],
        @"--no-first-run",
        @"--no-default-browser-check",
        @"https://glados.cloud/console/checkin"
    ];
    NSError *launchError = nil;
    if (![task launchAndReturnError:&launchError]) {
        ShowSessionAlert(@"无法打开 GLaDOS", launchError.localizedDescription ?: @"Microsoft Edge 启动失败。");
        return NO;
    }
    return YES;
}

@implementation GLaDOSPolicyMenuTarget
- (void)openPolicyEditor:(id)sender {
    NSString *bundlePath = [[NSBundle mainBundle] bundlePath];
    NSString *libraryPath = [bundlePath stringByAppendingPathComponent:@"Contents/Frameworks/GLaDOSPolicyEditor.dylib"];

    if (![[NSFileManager defaultManager] fileExistsAtPath:libraryPath]) {
        ShowSessionAlert(@"账号兑换方案模块不可用", @"未找到 Account Center 内置的兑换方案模块。请重新安装 GLaDOS Account Center。");
        return;
    }

    if (!policyEditorHandle) {
        policyEditorHandle = dlopen(libraryPath.fileSystemRepresentation, RTLD_NOW | RTLD_LOCAL);
    }
    if (!policyEditorHandle) {
        const char *message = dlerror();
        ShowSessionAlert(@"无法加载账号兑换方案模块", message ? [NSString stringWithUTF8String:message] : @"未知错误");
        return;
    }

    dlerror();
    GLaDOSShowPolicyEditorFunction showEditor = (GLaDOSShowPolicyEditorFunction)dlsym(policyEditorHandle, "GLaDOSShowPolicyEditor");
    const char *symbolError = dlerror();
    if (!showEditor || symbolError) {
        ShowSessionAlert(@"账号兑换方案模块版本不兼容", symbolError ? [NSString stringWithUTF8String:symbolError] : @"未找到窗口入口。");
        return;
    }

    showEditor();
}
@end

@implementation GLaDOSAccountSessionTarget
- (void)openAccountGlados:(NSMenuItem *)sender {
    NSString *accountKey = objc_getAssociatedObject(sender, AccountKeyAssociation);
    LaunchAccountEdgeProfile(accountKey);
}
@end

static GLaDOSPolicyMenuTarget *policyMenuTarget = nil;
static GLaDOSAccountSessionTarget *accountSessionTarget = nil;

static BOOL IsMainAccountCenterWindow(NSWindow *window) {
    if (!window || !(window.styleMask & NSWindowStyleMaskTitled)) return NO;
    static NSSet<NSString *> *titles = nil;
    static dispatch_once_t onceToken;
    dispatch_once(&onceToken, ^{
        titles = [NSSet setWithArray:@[@"概览", @"账号", @"签到日历", @"运行记录", @"积分方案", @"设置"]];
    });
    return [titles containsObject:window.title ?: @""];
}

static void InstallPolicyButtonOnWindow(NSWindow *window) {
    if (!policyMenuTarget || !IsMainAccountCenterWindow(window)) return;
    for (NSTitlebarAccessoryViewController *controller in window.titlebarAccessoryViewControllers) {
        if ([controller.view.identifier isEqualToString:PolicyButtonIdentifier]) return;
    }

    NSButton *button = [NSButton buttonWithTitle:@"兑换方案" target:policyMenuTarget action:@selector(openPolicyEditor:)];
    button.bezelStyle = NSBezelStyleTexturedRounded;
    button.controlSize = NSControlSizeRegular;
    button.toolTip = @"调整每个 GLaDOS 账号使用的兑换方案";
    [button sizeToFit];

    NSRect buttonFrame = button.frame;
    NSView *container = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, buttonFrame.size.width + 12.0, 28.0)];
    container.identifier = PolicyButtonIdentifier;
    button.frame = NSMakeRect(6.0, floor((container.bounds.size.height - buttonFrame.size.height) / 2.0), buttonFrame.size.width, buttonFrame.size.height);
    [container addSubview:button];

    NSTitlebarAccessoryViewController *accessory = [[NSTitlebarAccessoryViewController alloc] init];
    accessory.view = container;
    accessory.layoutAttribute = NSLayoutAttributeRight;
    [window addTitlebarAccessoryViewController:accessory];
}

static void InstallPolicyButtonOnAllWindows(void) {
    for (NSWindow *window in NSApp.windows) InstallPolicyButtonOnWindow(window);
}

static BOOL LooksLikeAccountActionMenu(NSMenu *menu) {
    return [menu itemWithTitle:AccountMenuOpenTitle] != nil &&
           [menu itemWithTitle:@"编辑备注名称"] != nil &&
           [menu itemWithTitle:@"移除自动化"] != nil;
}

static void PrepareAccountActionMenu(NSMenu *menu) {
    if (!LooksLikeAccountActionMenu(menu)) return;
    if (!accountSessionTarget) accountSessionTarget = [[GLaDOSAccountSessionTarget alloc] init];

    NSMenuItem *openItem = [menu itemWithTitle:AccountMenuOpenTitle];
    NSString *accountKey = AccountKeyNearCurrentMenu();
    objc_setAssociatedObject(openItem, AccountKeyAssociation,
                             IsValidAccountKey(accountKey) ? [accountKey uppercaseString] : nil,
                             OBJC_ASSOCIATION_COPY_NONATOMIC);
    openItem.target = accountSessionTarget;
    openItem.action = @selector(openAccountGlados:);
}

static void InstallPolicyMenu(void) {
    if (!NSApp || !NSApp.mainMenu) return;
    NSMenuItem *appMenuItem = [NSApp.mainMenu itemAtIndex:0];
    NSMenu *appMenu = appMenuItem.submenu;
    if (!appMenu) return;

    if (!policyMenuTarget) policyMenuTarget = [[GLaDOSPolicyMenuTarget alloc] init];

    BOOL hasPolicyMenuItem = NO;
    for (NSMenuItem *item in appMenu.itemArray) {
        if ([item.title isEqualToString:@"账号兑换方案…"]) {
            hasPolicyMenuItem = YES;
            break;
        }
    }

    if (!hasPolicyMenuItem) {
        NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:@"账号兑换方案…" action:@selector(openPolicyEditor:) keyEquivalent:@","];
        item.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagOption;
        item.target = policyMenuTarget;
        NSInteger insertionIndex = MIN((NSInteger)appMenu.numberOfItems, 2);
        [appMenu insertItem:item atIndex:insertionIndex];
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
            if ([note.object isKindOfClass:[NSWindow class]]) InstallPolicyButtonOnWindow((NSWindow *)note.object);
        }];
        [[NSNotificationCenter defaultCenter] addObserverForName:NSMenuDidBeginTrackingNotification
                                                          object:nil
                                                           queue:[NSOperationQueue mainQueue]
                                                      usingBlock:^(NSNotification *note) {
            if ([note.object isKindOfClass:[NSMenu class]]) PrepareAccountActionMenu((NSMenu *)note.object);
        }];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.0 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            InstallPolicyMenu();
            InstallPolicyButtonOnAllWindows();
        });
    });
}
