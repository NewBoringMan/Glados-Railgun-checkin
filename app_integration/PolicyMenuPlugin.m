#import <AppKit/AppKit.h>

@interface GLaDOSPolicyMenuTarget : NSObject
- (void)openPolicyEditor:(id)sender;
@end

@implementation GLaDOSPolicyMenuTarget
- (void)openPolicyEditor:(id)sender {
    NSString *bundlePath = [[NSBundle mainBundle] bundlePath];
    NSString *helperPath = [bundlePath stringByAppendingPathComponent:@"Contents/Applications/GLaDOS Exchange Policy.app"];
    NSURL *helperURL = [NSURL fileURLWithPath:helperPath];
    if (![[NSFileManager defaultManager] fileExistsAtPath:helperPath]) {
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"账号兑换方案编辑器不可用";
        alert.informativeText = @"未找到内置的 GLaDOS Exchange Policy.app。请重新安装 Account Center V2.0.7。";
        [alert runModal];
        return;
    }

    NSWorkspaceOpenConfiguration *configuration = [NSWorkspaceOpenConfiguration configuration];
    configuration.activates = YES;
    [[NSWorkspace sharedWorkspace] openApplicationAtURL:helperURL
                                          configuration:configuration
                                      completionHandler:^(NSRunningApplication * _Nullable app, NSError * _Nullable error) {
        if (error) {
            dispatch_async(dispatch_get_main_queue(), ^{
                NSAlert *alert = [[NSAlert alloc] init];
                alert.messageText = @"无法打开账号兑换方案编辑器";
                alert.informativeText = error.localizedDescription ?: @"未知错误";
                [alert runModal];
            });
        }
    }];
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
