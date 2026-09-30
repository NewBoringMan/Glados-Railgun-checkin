// Internal, data-only Scripting Bridge adapter for the installed Apple Mail.
// No GUI, launch, quit, scripts, passwords, database access or mailbox writes.
#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#import <Carbon/Carbon.h>
#import <ScriptingBridge/ScriptingBridge.h>
#import "Mail.h"
static NSDictionary *Failure(NSString *s) { return @{@"ok":@NO,@"reason":s}; }
static BOOL IsString(id v) { return [v isKindOfClass:NSString.class]; }
static NSString *NormalizeEmail(id v) {
    if (!IsString(v)) return nil;
    NSString *s = [[v stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] lowercaseString];
    return s.length <= 254 && [s rangeOfString:@"^[^\\s<>@,;]+@[^\\s<>@,;]+\\.[^\\s<>@,;]+$" options:NSRegularExpressionSearch].location != NSNotFound ? s : nil;
}
static BOOL IsCodeSubject(NSString *v) {
    if (!IsString(v)) return NO;
    NSString *s = [v stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    NSRegularExpression *r = [NSRegularExpression regularExpressionWithPattern:@"^(?:(?:fw|fwd|转发|轉寄)\\s*[:：]\\s*)+" options:NSRegularExpressionCaseInsensitive error:nil];
    s = [r stringByReplacingMatchesInString:s options:0 range:NSMakeRange(0,s.length) withTemplate:@""];
    return [[[s stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] lowercaseString] isEqualToString:@"glados authentication code"];
}
static NSDictionary *ValidateRequest(NSDictionary *q) {
    if (![q isKindOfClass:NSDictionary.class] || !IsString(q[@"op"])) return Failure(@"invalid_mail_request");
    NSString *op = q[@"op"];
    if (![@[@"permission",@"authorize",@"verify",@"check",@"list",@"read"] containsObject:op]) return Failure(@"invalid_mail_request");
    NSMutableSet *allowed = [NSMutableSet setWithObject:@"op"];
    if (![@[@"permission",@"authorize"] containsObject:op]) {
        [allowed addObject:@"mailbox"]; if (!NormalizeEmail(q[@"mailbox"])) return Failure(@"invalid_email");
    }
    if ([op isEqualToString:@"check"] && q[@"origin_email"]) {
        [allowed addObject:@"origin_email"]; if (!NormalizeEmail(q[@"origin_email"])) return Failure(@"invalid_email");
    }
    if ([op isEqualToString:@"list"]) {
        [allowed addObject:@"after"]; id v = q[@"after"];
        if (![v isKindOfClass:NSNumber.class] || CFGetTypeID((__bridge CFTypeRef)v) == CFBooleanGetTypeID() || !isfinite([v doubleValue])) return Failure(@"invalid_mail_window");
    }
    if ([op isEqualToString:@"read"]) {
        [allowed addObject:@"message_id"]; NSString *mid = q[@"message_id"];
        if (!IsString(mid) || [mid rangeOfString:@"^mail:[1-9][0-9]{0,14}$" options:NSRegularExpressionSearch].location == NSNotFound) return Failure(@"invalid_mail_id");
    }
    for (NSString *k in q) if (![allowed containsObject:k]) return Failure(@"invalid_mail_request");
    return nil;
}
@interface MailErrors : NSObject <SBApplicationDelegate>
@property(nonatomic,strong) NSError *error;
@end
@implementation MailErrors
- (id)eventDidFail:(const AppleEvent *)event withError:(NSError *)error { self.error = error; return nil; }
@end
static NSDictionary *Permission(BOOL ask) {
    const char *name = "com.apple.mail"; AEAddressDesc target = {typeNull,NULL};
    if (AECreateDesc(typeApplicationBundleID,name,strlen(name),&target) != noErr) return Failure(@"mail_permission_unavailable");
    OSStatus code = AEDeterminePermissionToAutomateTarget(&target,kAECoreSuite,kAEGetData,ask); AEDisposeDesc(&target);
    NSString *state = code == noErr ? @"granted" : code == errAEEventWouldRequireUserConsent ? @"required" : code == errAEEventNotPermitted ? @"denied" : @"unavailable";
    return @{@"ok":@YES,@"permission":state,@"status":@(code)};
}
static MailAccount *FindAccount(MailApplication *app,NSString *email,MailErrors *e) {
    SBElementArray *accounts = app.accounts; if (accounts.count > 100 || e.error) return nil;
    MailAccount *match = nil;
    for (MailAccount *a in accounts) {
        BOOL found = NO;
        for (NSString *address in a.emailAddresses) if ([NormalizeEmail(address) isEqualToString:email]) found = YES;
        if (e.error) return nil;
        if (found) { if (match || !a.enabled) return nil; match = a; }
    }
    return match;
}
static MailMailbox *FindInbox(MailAccount *account,MailErrors *e) {
    SBElementArray *boxes = account.mailboxes; if (boxes.count > 250 || e.error) return nil;
    MailMailbox *match = nil;
    for (MailMailbox *box in boxes) {
        if ([@[@"inbox",@"收件箱",@"收件匣"] containsObject:[box.name lowercaseString]]) { if (match) return nil; match = box; }
        if (e.error) return nil;
    }
    return match;
}
static NSDictionary *Perform(NSDictionary *q) {
    NSDictionary *invalid = ValidateRequest(q); if (invalid) return invalid;
    if (![[NSBundle mainBundle].bundleIdentifier isEqualToString:@"com.enoch.glados-account-center"]) return Failure(@"installed_bundle_required");
    NSArray<NSRunningApplication *> *running = [NSRunningApplication runningApplicationsWithBundleIdentifier:@"com.apple.mail"];
    if (running.count != 1) return Failure(running.count == 0 ? @"mail_not_running" : @"mail_instance_ambiguous");
    NSString *op = q[@"op"]; NSDictionary *permission = Permission([op isEqualToString:@"authorize"]);
    if ([@[@"permission",@"authorize"] containsObject:op]) return permission;
    if (![permission[@"permission"] isEqualToString:@"granted"]) return Failure(@"mail_permission_required");
    NSString *email = NormalizeEmail(q[@"mailbox"]);
    MailApplication *app = [SBApplication applicationWithProcessIdentifier:running.firstObject.processIdentifier];
    app.timeout = 15; app.sendMode = kAEWaitReply | kAENeverInteract;
    MailErrors *e = [MailErrors new]; app.delegate = e;
    MailAccount *account = FindAccount(app,email,e);
    if (e.error) return Failure(@"mail_data_unavailable"); if (!account) return Failure(@"mail_receiver_not_found");
    MailMailbox *inbox = FindInbox(account,e);
    if (e.error) return Failure(@"mail_data_unavailable"); if (!inbox) return Failure(@"mail_inbox_unavailable");
    if ([op isEqualToString:@"verify"]) return @{@"ok":@YES,@"mailbox":email,@"permission":@"granted",@"inbox_found":@YES,@"online_delivery_proven":@NO};
    if ([op isEqualToString:@"check"]) {
        [app checkForNewMailFor:account]; NSString *origin = NormalizeEmail(q[@"origin_email"]);
        if (origin && ![origin isEqualToString:email] && !e.error) {
            MailAccount *source = FindAccount(app,origin,e); if (source && !e.error) [app checkForNewMailFor:source];
        }
        return e.error ? Failure(@"mail_check_failed") : @{@"ok":@YES,@"check_requested":@YES,@"delivery_complete":@NO};
    }
    NSTimeInterval now = NSDate.date.timeIntervalSince1970;
    NSTimeInterval cutoff = [op isEqualToString:@"list"] ? [q[@"after"] doubleValue] : now-86400;
    if (cutoff < now-86400 || cutoff > now+15) return Failure(@"invalid_mail_window");
    NSArray<MailMessage *> *messages = [inbox.messages filteredArrayUsingPredicate:[NSPredicate predicateWithFormat:@"dateReceived >= %@ AND subject CONTAINS[cd] %@",[NSDate dateWithTimeIntervalSince1970:cutoff],@"GLaDOS Authentication Code"]];
    if (e.error) return Failure(@"mail_data_unavailable"); if (messages.count > 100) return Failure(@"mail_window_too_large");
    NSMutableArray *ids = [NSMutableArray array];
    for (MailMessage *m in messages) {
        NSString *subject = m.subject; if (e.error) return Failure(@"mail_data_unavailable"); if (!IsCodeSubject(subject)) continue;
        NSInteger mid = m.id; if (e.error || mid < 1) return Failure(@"invalid_mail_id");
        NSString *identifier = [NSString stringWithFormat:@"mail:%ld",(long)mid]; [ids addObject:identifier];
        if ([op isEqualToString:@"read"] && [identifier isEqualToString:q[@"message_id"]]) {
            NSString *source = m.source; NSDate *received = m.dateReceived;
            if (e.error || !IsString(source) || ![received isKindOfClass:NSDate.class]) return Failure(@"mail_source_unavailable");
            NSData *raw = [source dataUsingEncoding:NSUTF8StringEncoding];
            if (!raw.length || raw.length > 524288) return Failure(@"invalid_mail_size");
            return @{@"ok":@YES,@"mailbox":email,@"message_id":identifier,@"received_at":@(received.timeIntervalSince1970),@"source_base64":[raw base64EncodedStringWithOptions:0]};
        }
    }
    if ([op isEqualToString:@"read"]) return Failure(@"mail_message_unavailable");
    return @{@"ok":@YES,@"mailbox":email,@"message_ids":ids};
}
int main(int argc,const char *argv[]) {
    @autoreleasepool {
        NSDictionary *result;
        if (argc == 2 && strcmp(argv[1],"--self-test") == 0) {
            BOOL pass = IsCodeSubject(@"Fwd: GLaDOS Authentication Code") && !IsCodeSubject(@"GLaDOS Authentication Code marketing") && [NormalizeEmail(@" Person@Example.com ") isEqualToString:@"person@example.com"] && ValidateRequest(@{@"op":@"read",@"mailbox":@"codes@example.net",@"message_id":@"../private"}) && ValidateRequest(@{@"op":@"verify",@"mailbox":@"codes@example.net",@"service":@"other"});
            result = @{@"ok":@(pass),@"self_test":@"syntax_and_scope",@"mail_contacted":@NO,@"ui_requested":@NO};
        } else if (argc != 1) result = Failure(@"stdin_only");
        else {
            @try {
                NSMutableData *data = [NSMutableData data];
                while (data.length <= 16384) {
                    NSData *part = [[NSFileHandle fileHandleWithStandardInput] readDataOfLength:MIN((NSUInteger)4096,16385-data.length)];
                    if (!part.length) break; [data appendData:part];
                }
                id q = data.length <= 16384 ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil; result = Perform(q);
            } @catch (__unused NSException *exception) { result = Failure(@"mail_data_unavailable"); }
        }
        NSData *data = [NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingSortedKeys error:nil];
        [[NSFileHandle fileHandleWithStandardOutput] writeData:data];
        [[NSFileHandle fileHandleWithStandardOutput] writeData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
        return [result[@"ok"] boolValue] ? 0 : 1;
    }
}
