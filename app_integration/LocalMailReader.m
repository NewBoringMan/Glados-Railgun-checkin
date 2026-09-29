// Internal data-only Mail adapter. Uses Mail's public Scripting Bridge dictionary.
// Does not drive GUI, activate/quit/launch Mail, run AppleScript, inspect Mail files,
// read passwords, change rules, mark messages read, or modify any mailbox.
#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#import <Carbon/Carbon.h>
#import <ScriptingBridge/ScriptingBridge.h>
#import "Mail.h"

static NSString *const OwnerBundle = @"com.enoch.glados-account-center";
static const NSUInteger MaximumSourceBytes = 524288;
static NSDictionary *Failure(NSString *reason) { return @{@"ok":@NO, @"reason":reason}; }
static BOOL IsString(id value) { return [value isKindOfClass:NSString.class]; }
static NSString *NormalizeEmail(id value) {
    if (!IsString(value)) return nil;
    NSString *email = [[value stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] lowercaseString];
    if (email.length > 254 || [email rangeOfString:@"^[^\\s<>@,;]+@[^\\s<>@,;]+\\.[^\\s<>@,;]+$" options:NSRegularExpressionSearch].location == NSNotFound) return nil;
    return email;
}
static NSString *CanonicalSubject(NSString *value) {
    NSString *text = [value stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    NSRegularExpression *prefix = [NSRegularExpression regularExpressionWithPattern:@"^(?:(?:fw|fwd|转发|轉寄)\\s*[:：]\\s*)+" options:NSRegularExpressionCaseInsensitive error:nil];
    text = [prefix stringByReplacingMatchesInString:text options:0 range:NSMakeRange(0,text.length) withTemplate:@""];
    return [[text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] lowercaseString];
}
static BOOL IsCodeSubject(NSString *value) {
    return IsString(value) && [CanonicalSubject(value) isEqualToString:@"glados authentication code"];
}
static BOOL ValidTime(id value) {
    return [value isKindOfClass:NSNumber.class] && CFGetTypeID((__bridge CFTypeRef)value) != CFBooleanGetTypeID() && isfinite([value doubleValue]);
}
static NSDictionary *ValidateRequest(NSDictionary *input) {
    if (![input isKindOfClass:NSDictionary.class] || !IsString(input[@"op"])) return Failure(@"invalid_mail_request");
    NSString *op = input[@"op"];
    NSSet *operations = [NSSet setWithArray:@[@"permission",@"authorize",@"verify",@"check",@"list",@"read"]];
    if (![operations containsObject:op]) return Failure(@"invalid_mail_request");
    NSMutableSet *allowed = [NSMutableSet setWithArray:@[@"op"]];
    if (![@[@"permission",@"authorize"] containsObject:op]) {
        [allowed addObject:@"mailbox"];
        if (!NormalizeEmail(input[@"mailbox"])) return Failure(@"invalid_email");
    }
    if ([op isEqualToString:@"check"] && input[@"origin_email"]) {
        [allowed addObject:@"origin_email"];
        if (!NormalizeEmail(input[@"origin_email"])) return Failure(@"invalid_email");
    }
    if ([op isEqualToString:@"list"]) {
        [allowed addObject:@"after"];
        if (!ValidTime(input[@"after"])) return Failure(@"invalid_mail_window");
    }
    if ([op isEqualToString:@"read"]) {
        [allowed addObject:@"message_id"];
        NSString *mid = input[@"message_id"];
        if (!IsString(mid) || [mid rangeOfString:@"^mail:[1-9][0-9]{0,14}$" options:NSRegularExpressionSearch].location == NSNotFound) return Failure(@"invalid_mail_id");
    }
    for (NSString *key in input) if (![allowed containsObject:key]) return Failure(@"invalid_mail_request");
    return nil;
}
@interface MailErrors : NSObject <SBApplicationDelegate>
@property(nonatomic,strong) NSError *error;
@end
@implementation MailErrors
- (id)eventDidFail:(const AppleEvent *)event withError:(NSError *)error { self.error = error; return nil; }
@end
static NSDictionary *Permission(BOOL ask) {
    const char *bundle = "com.apple.mail";
    AEAddressDesc target = {typeNull,NULL};
    OSStatus made = AECreateDesc(typeApplicationBundleID,bundle,strlen(bundle),&target);
    if (made != noErr) return Failure(@"mail_permission_unavailable");
    OSStatus result = AEDeterminePermissionToAutomateTarget(&target,kAECoreSuite,kAEGetData,ask);
    AEDisposeDesc(&target);
    NSString *state = result == noErr ? @"granted" : result == errAEEventWouldRequireUserConsent ? @"required" : result == errAEEventNotPermitted ? @"denied" : @"unavailable";
    return @{@"ok":@YES,@"permission":state,@"status":@(result)};
}
static MailAccount *FindAccount(MailApplication *app,NSString *mailbox,MailErrors *errors) {
    SBElementArray *accounts = app.accounts;
    if (accounts.count > 100 || errors.error) return nil;
    MailAccount *match = nil;
    for (MailAccount *account in accounts) {
        BOOL matches = NO;
        for (NSString *address in account.emailAddresses) if ([NormalizeEmail(address) isEqualToString:mailbox]) matches = YES;
        if (errors.error) return nil;
        if (matches) {
            if (match != nil || !account.enabled) return nil;
            match = account;
        }
    }
    return match;
}
static MailMailbox *FindInbox(MailAccount *account,MailErrors *errors) {
    SBElementArray *boxes = account.mailboxes;
    if (boxes.count > 250 || errors.error) return nil;
    MailMailbox *match = nil;
    NSSet *inboxNames = [NSSet setWithArray:@[@"inbox",@"收件箱",@"收件匣"]];
    for (MailMailbox *box in boxes) {
        if ([inboxNames containsObject:[box.name lowercaseString]]) {
            if (match != nil) return nil;
            match = box;
        }
        if (errors.error) return nil;
    }
    return match;
}
static NSDictionary *Perform(NSDictionary *input) {
    NSDictionary *invalid = ValidateRequest(input);
    if (invalid) return invalid;
    if (![[NSBundle mainBundle].bundleIdentifier isEqualToString:OwnerBundle]) return Failure(@"installed_bundle_required");
    NSString *op = input[@"op"];
    NSArray<NSRunningApplication *> *running = [NSRunningApplication runningApplicationsWithBundleIdentifier:@"com.apple.mail"];
    if (running.count != 1) return Failure(running.count == 0 ? @"mail_not_running" : @"mail_instance_ambiguous");
    NSDictionary *permission = Permission([op isEqualToString:@"authorize"]);
    if ([@[@"permission",@"authorize"] containsObject:op]) return permission;
    if (![permission[@"permission"] isEqualToString:@"granted"]) return Failure(@"mail_permission_required");
    NSString *mailbox = NormalizeEmail(input[@"mailbox"]);
    MailApplication *app = [SBApplication applicationWithProcessIdentifier:running.firstObject.processIdentifier];
    app.timeout = 15;
    app.sendMode = kAEWaitReply | kAENeverInteract;
    MailErrors *errors = [MailErrors new]; app.delegate = errors;
    MailAccount *account = FindAccount(app,mailbox,errors);
    if (errors.error) return Failure(@"mail_data_unavailable");
    if (!account) return Failure(@"mail_receiver_not_found");
    MailMailbox *inbox = FindInbox(account,errors);
    if (errors.error) return Failure(@"mail_data_unavailable");
    if (!inbox) return Failure(@"mail_inbox_unavailable");
    if ([op isEqualToString:@"verify"]) return @{@"ok":@YES,@"mailbox":mailbox,@"permission":@"granted",@"inbox_found":@YES,@"online_delivery_proven":@NO};
    if ([op isEqualToString:@"check"]) {
        [app checkForNewMailFor:account];
        NSString *origin = NormalizeEmail(input[@"origin_email"]);
        if (origin && ![origin isEqualToString:mailbox] && !errors.error) {
            MailAccount *sourceAccount = FindAccount(app,origin,errors);
            if (sourceAccount && !errors.error) [app checkForNewMailFor:sourceAccount];
        }
        return errors.error ? Failure(@"mail_check_failed") : @{@"ok":@YES,@"check_requested":@YES,@"delivery_complete":@NO};
    }
    NSTimeInterval now = NSDate.date.timeIntervalSince1970;
    NSTimeInterval cutoff = [op isEqualToString:@"list"] ? [input[@"after"] doubleValue] : now-86400;
    if (cutoff < now-86400 || cutoff > now+15) return Failure(@"invalid_mail_window");
    NSDate *after = [NSDate dateWithTimeIntervalSince1970:cutoff];
    NSArray<MailMessage *> *messages = [inbox.messages filteredArrayUsingPredicate:
        [NSPredicate predicateWithFormat:@"dateReceived >= %@ AND subject CONTAINS[cd] %@",after,@"GLaDOS Authentication Code"]];
    if (errors.error) return Failure(@"mail_data_unavailable");
    if (messages.count > 100) return Failure(@"mail_window_too_large");
    NSMutableArray *ids = [NSMutableArray array];
    for (MailMessage *message in messages) {
        NSString *subject = message.subject;
        if (errors.error) return Failure(@"mail_data_unavailable");
        if (!IsCodeSubject(subject)) continue;
        NSInteger mid = message.id;
        if (errors.error || mid < 1) return Failure(@"invalid_mail_id");
        NSString *identifier = [NSString stringWithFormat:@"mail:%ld",(long)mid];
        [ids addObject:identifier];
        if ([op isEqualToString:@"read"] && [identifier isEqualToString:input[@"message_id"]]) {
            NSString *source = message.source;
            NSDate *received = message.dateReceived;
            if (errors.error || !IsString(source) || ![received isKindOfClass:NSDate.class]) return Failure(@"mail_source_unavailable");
            NSData *raw = [source dataUsingEncoding:NSUTF8StringEncoding];
            if (!raw.length || raw.length > MaximumSourceBytes) return Failure(@"invalid_mail_size");
            return @{@"ok":@YES,@"mailbox":mailbox,@"message_id":identifier,
                     @"received_at":@(received.timeIntervalSince1970),@"source_base64":[raw base64EncodedStringWithOptions:0]};
        }
    }
    if ([op isEqualToString:@"read"]) return Failure(@"mail_message_unavailable");
    return @{@"ok":@YES,@"mailbox":mailbox,@"message_ids":ids};
}
int main(int argc,const char *argv[]) {
    @autoreleasepool {
        NSDictionary *result;
        if (argc == 2 && strcmp(argv[1],"--self-test") == 0) {
            BOOL passed = IsCodeSubject(@"Fwd: GLaDOS Authentication Code") &&
                !IsCodeSubject(@"GLaDOS Authentication Code marketing") &&
                [NormalizeEmail(@" Person@Example.com ") isEqualToString:@"person@example.com"] &&
                ValidateRequest(@{@"op":@"read",@"mailbox":@"codes@example.net",@"message_id":@"../private"}) != nil &&
                ValidateRequest(@{@"op":@"verify",@"mailbox":@"codes@example.net",@"service":@"other"}) != nil;
            result = @{@"ok":@(passed),@"self_test":@"syntax_and_scope",@"mail_contacted":@NO,@"ui_requested":@NO};
        } else if (argc != 1) result = Failure(@"stdin_only");
        else {
            @try {
                NSMutableData *data = [NSMutableData data];
                while (data.length <= 16384) {
                    NSData *part = [NSFileHandle.standardInput readDataOfLength:MIN((NSUInteger)4096,16385-data.length)];
                    if (!part.length) break;
                    [data appendData:part];
                }
                id input = data.length <= 16384 ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
                result = Perform(input);
            } @catch (__unused NSException *exception) { result = Failure(@"mail_data_unavailable"); }
        }
        NSData *encoded = [NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingSortedKeys error:nil];
        [NSFileHandle.standardOutput writeData:encoded];
        [NSFileHandle.standardOutput writeData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
        return [result[@"ok"] boolValue] ? 0 : 1;
    }
}
