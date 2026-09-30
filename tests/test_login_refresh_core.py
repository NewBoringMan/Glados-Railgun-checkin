import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from login_refresh_core import (
    RefreshError, RefreshStore, MailEnvelope, LoginAttempt,
    parse_candidate, select_code, next_month, parse_date, email_address,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 29, 6, 45, 0, tzinfo=UTC)
REQUEST = NOW - timedelta(seconds=60)
KEY_A = 'A' * 16
KEY_B = 'B' * 16
KEY_C = 'C' * 16
TARGET = 'person@example.com'
MAILBOX = 'codes@example.net'
SENDER = 'noreply@glados.network'
CYCLE = '2026-09'


def email_fixture(target=TARGET, code='001234', issued=None, *, forwarded=True,
                  html=False, original_sender=SENDER, extra='', remove_header=None):
    issued = issued or REQUEST + timedelta(seconds=2)
    stamp = issued.strftime('%a, %d %b %Y %H:%M:%S +0000')
    message = EmailMessage()
    message['From'] = TARGET if forwarded else original_sender
    message['To'] = MAILBOX if forwarded else target
    message['Subject'] = ('Fwd: ' if forwarded else '') + 'GLaDOS Authentication Code'
    message['Date'] = NOW.strftime('%a, %d %b %Y %H:%M:%S +0000') if forwarded else stamp
    body = ''
    if forwarded:
        headers = {'From':f'GLaDOS <{original_sender}>', 'To': target, 'Date':stamp,
                   'Subject':'GLaDOS Authentication Code'}
        if remove_header:
            headers.pop(remove_header)
        body = 'Begin forwarded message:\n\n' + '\n'.join(f'{k}: {v}' for k,v in headers.items()) + '\n\n'
    body += f'Your verification code is:\n{code}\nThis code will expire in 10 minutes.\n{extra}'
    if html:
        message.set_content('<html><body>' + ''.join(f'<div>{line.replace("<", "&lt;").replace(">", "&gt;")}</div>' for line in body.splitlines()) + '</body></html>', subtype='html')
    else:
        message.set_content(body)
    return message.as_bytes()


def envelope(raw=None, **kwargs):
    fields = dict(gmail_id='new-1', mailbox=MAILBOX,
                  received_at=NOW - timedelta(seconds=20), authenticated_sender=TARGET,
                  raw=raw if raw is not None else email_fixture())
    fields.update(kwargs)
    return MailEnvelope(**fields)


def attempt(**kwargs):
    fields = dict(attempt_id='attempt-1', target_email=TARGET, mailbox=MAILBOX,
                  requested_at=REQUEST, allowed_forwarders=frozenset({TARGET}))
    fields.update(kwargs)
    return LoginAttempt(**fields)


class MailTests(unittest.TestCase):
    def assert_reason(self, reason, env=None, context=None, now=NOW):
        with self.assertRaisesRegex(RefreshError, '^' + reason + '$'):
            parse_candidate(env or envelope(), context or attempt(), now)

    def test_plain_forward(self):
        value = parse_candidate(envelope(), attempt(), NOW)
        self.assertEqual(value.code, '001234')
        self.assertEqual(value.target_email, TARGET)

    def test_html_forward(self):
        value = parse_candidate(envelope(email_fixture(html=True)), attempt(), NOW)
        self.assertEqual(value.code, '001234')

    def test_direct_delivery(self):
        value = parse_candidate(envelope(email_fixture(forwarded=False), authenticated_sender=SENDER), attempt(), NOW)
        self.assertEqual(value.code, '001234')

    def test_rfc822_attachment_forward(self):
        outer = EmailMessage()
        outer['From'] = TARGET
        outer['To'] = MAILBOX
        outer['Subject'] = 'Fwd: GLaDOS Authentication Code'
        outer.set_content('Forwarded original attached.')
        from email.parser import BytesParser
        from email import policy
        inner = BytesParser(policy=policy.default).parsebytes(email_fixture(forwarded=False))
        outer.add_attachment(inner)
        self.assertEqual(parse_candidate(envelope(outer.as_bytes()), attempt(), NOW).code, '001234')

    def test_chinese_forward_headers(self):
        raw = EmailMessage()
        raw['From'] = TARGET
        raw['To'] = MAILBOX
        raw['Subject'] = '转发: GLaDOS Authentication Code'
        raw.set_content(f'下面是被转发的邮件：\n发件人: GLaDOS <{SENDER}>\n主题: GLaDOS Authentication Code\n日期: 2026年9月29日 GMT+8 14:44:02\n收件人: {TARGET}\nYour verification code is:\n001234\n')
        self.assertEqual(parse_candidate(envelope(raw.as_bytes()), attempt(), NOW).code, '001234')

    def test_old_original_with_new_forward_arrival_is_rejected(self):
        # Shape of the observed sample: original yesterday, forwarded today.
        self.assert_reason('not_issued_for_attempt', envelope(email_fixture(issued=NOW-timedelta(days=1))))

    def test_original_before_current_request(self):
        self.assert_reason('not_issued_for_attempt', envelope(email_fixture(issued=REQUEST-timedelta(seconds=1))))

    def test_second_precision_date_matches_subsecond_request(self):
        requested = REQUEST.replace(microsecond=500000)
        value = parse_candidate(envelope(email_fixture(issued=REQUEST)), attempt(requested_at=requested), NOW)
        self.assertEqual(value.code, '001234')

    def test_previous_second_does_not_match_subsecond_request(self):
        requested = REQUEST.replace(microsecond=500000)
        self.assert_reason('not_issued_for_attempt', envelope(email_fixture(issued=REQUEST-timedelta(seconds=1))), attempt(requested_at=requested))

    def test_wrong_original_recipient(self):
        self.assert_reason('wrong_original_recipient', envelope(email_fixture(target='other@example.com')))

    def test_no_plus_address_alias_collapse(self):
        self.assert_reason('wrong_original_recipient', envelope(email_fixture(target='person+other@example.com')))

    def test_wrong_mailbox(self):
        self.assert_reason('wrong_mailbox', envelope(mailbox='different@example.net'))

    def test_untrusted_forwarder(self):
        self.assert_reason('untrusted_forwarder', context=attempt(allowed_forwarders=frozenset()))

    def test_authentication_is_required(self):
        self.assert_reason('untrusted_sender', envelope(authenticated_sender=None))

    def test_authenticated_sender_mismatch(self):
        self.assert_reason('untrusted_sender', envelope(authenticated_sender='attacker@example.com'))

    def test_wrong_original_sender(self):
        self.assert_reason('wrong_original_sender', envelope(email_fixture(original_sender='noreply@evil.example')))

    def test_missing_original_date(self):
        self.assert_reason('incomplete_original_headers', envelope(email_fixture(remove_header='Date')))

    def test_repeated_forward_header(self):
        self.assert_reason('ambiguous_forward', envelope(email_fixture(extra='To: other@example.com')))

    def test_baseline_message_is_not_new(self):
        self.assert_reason('not_new_for_attempt', context=attempt(baseline_ids=frozenset({'new-1'})))

    def test_arrival_before_request(self):
        self.assert_reason('not_new_for_attempt', envelope(received_at=REQUEST-timedelta(seconds=1)))

    def test_future_arrival(self):
        self.assert_reason('not_new_for_attempt', envelope(received_at=NOW+timedelta(minutes=3)))

    def test_expired_attempt(self):
        self.assert_reason('attempt_expired', context=attempt(requested_at=NOW-timedelta(minutes=10)))

    def test_expiry_safety_margin(self):
        requested = NOW - timedelta(seconds=595)
        self.assert_reason('code_expired', envelope(email_fixture(issued=requested+timedelta(seconds=2))), attempt(requested_at=requested))

    def test_multiple_codes_in_one_body(self):
        self.assert_reason('missing_or_ambiguous_code', envelope(email_fixture(extra='Your verification code is: 998877')))

    def test_arbitrary_six_digits_not_accepted(self):
        self.assert_reason('missing_or_ambiguous_code', envelope(email_fixture(code='abcdef', extra='Order 998877')))

    def test_seven_digits_not_accepted(self):
        self.assert_reason('missing_or_ambiguous_code', envelope(email_fixture(code='1234567')))

    def test_deduplicate_same_code_forwarded_twice(self):
        value = select_code([envelope(), envelope(gmail_id='new-2')], attempt(), NOW)
        self.assertEqual(value.code, '001234')

    def test_conflicting_new_messages_fail_closed(self):
        with self.assertRaisesRegex(RefreshError, 'multiple_fresh_codes'):
            select_code([envelope(), envelope(email_fixture(code='998877'), gmail_id='new-2')], attempt(), NOW)

    def test_stale_message_is_ignored_beside_fresh(self):
        value = select_code([envelope(email_fixture(issued=NOW-timedelta(days=1)), gmail_id='old'), envelope()], attempt(), NOW)
        self.assertEqual(value.gmail_id, 'new-1')

    def test_no_valid_code_returns_none(self):
        self.assertIsNone(select_code([envelope(authenticated_sender=None)], attempt(), NOW))

    def test_code_and_raw_mail_hidden_from_repr(self):
        self.assertNotIn('001234', repr(envelope()))
        self.assertNotIn('001234', repr(parse_candidate(envelope(), attempt(), NOW)))

    def test_size_limit(self):
        self.assert_reason('invalid_mail_size', envelope(b'x' * (512*1024+1)))

    def test_timezone_required(self):
        self.assert_reason('timezone_required', context=attempt(requested_at=REQUEST.replace(tzinfo=None)))

    def test_ttl_not_allowed_above_observed_maximum(self):
        self.assert_reason('invalid_attempt', context=attempt(ttl_seconds=3600))

    def test_chinese_date(self):
        self.assertEqual(parse_date('2026年9月29日 GMT+8 14:44:02'), REQUEST+timedelta(seconds=2))

    def test_naive_original_date_is_rejected(self):
        with self.assertRaisesRegex(RefreshError, 'invalid_original_date'):
            parse_date('2026-09-29T14:44:02')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'private' / 'refresh.sqlite'
        self.store = RefreshStore(self.path)
        self.store.remember_verified_identity(KEY_A, TARGET, NOW)
        self.store.remember_verified_identity(KEY_B, 'second@example.com', NOW)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_email_survives_failure_cache_and_restart(self):
        self.store.import_successful_cache([{'account_key':KEY_A, 'ok':False, 'email':''}], NOW)
        self.store.close()
        self.store = RefreshStore(self.path)
        self.assertEqual(self.store.display_name(KEY_A), TARGET)

    def test_successful_cache_import(self):
        result = self.store.import_successful_cache([{'account_key':KEY_C, 'ok':True, 'email':'third@example.com'}], NOW)
        self.assertEqual(result['imported'], 1)
        self.assertEqual(self.store.identity(KEY_C)['email'], 'third@example.com')

    def test_identity_conflict_never_overwrites(self):
        with self.assertRaisesRegex(RefreshError, 'identity_mismatch'):
            self.store.remember_verified_identity(KEY_A, 'wrong@example.com', NOW)
        self.assertEqual(self.store.identity(KEY_A)['email'], TARGET)

    def test_duplicate_email_never_creates_new_account_identity(self):
        with self.assertRaisesRegex(RefreshError, 'duplicate_identity'):
            self.store.remember_verified_identity(KEY_C, TARGET, NOW)

    def test_no_identity_means_manual_not_guessed(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_C],NOW)
        self.assertEqual(self.store.job(CYCLE,KEY_C)['reason'], 'missing_identity')

    def test_failures_do_not_erase_display_name(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
            self.store.claim(CYCLE,NOW)
            self.store.request_started(CYCLE,KEY_A,NOW)
            self.store.fail(CYCLE,KEY_A,'mail_timeout',NOW)
        self.assertEqual(self.store.display_name(KEY_A),TARGET)

    def test_file_permissions(self):
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)

    def test_symlink_state_is_rejected(self):
        link=self.path.parent/'link.sqlite'
        link.symlink_to(self.path)
        with self.assertRaises(OSError): RefreshStore(link)

    def test_execution_lock_required(self):
        with self.assertRaisesRegex(RefreshError, 'execution_lock_required'):
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)

    def test_second_worker_cannot_run(self):
        other=RefreshStore(self.path)
        try:
            with self.store.exclusive_run():
                with self.assertRaisesRegex(RefreshError, 'already_running'):
                    with other.exclusive_run():
                        self.fail('second worker must not start')
        finally: other.close()

    def test_cycle_is_idempotent(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A,KEY_A],NOW)
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
        self.assertEqual(len(self.store.snapshot(CYCLE)),1)

    def test_first_round_finishes_before_failed_account_retry(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A,KEY_B],NOW)
            self.assertEqual(self.store.claim(CYCLE,NOW)['account_key'],KEY_A)
            self.store.request_started(CYCLE,KEY_A,NOW)
            self.store.fail(CYCLE,KEY_A,'mail_timeout',NOW)
            self.assertEqual(self.store.claim(CYCLE,NOW)['account_key'],KEY_B)

    def test_earlier_due_retry_not_blocked_by_alphabetic_key(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A,KEY_B],NOW)
            self.store.claim(CYCLE,NOW)
            self.store.request_started(CYCLE,KEY_A,NOW)
            self.store.fail(CYCLE,KEY_A,'mail_timeout',NOW+timedelta(minutes=5))
            self.store.claim(CYCLE,NOW)
            self.store.request_started(CYCLE,KEY_B,NOW)
            self.store.fail(CYCLE,KEY_B,'mail_timeout',NOW)
            self.assertEqual(self.store.claim(CYCLE,NOW+timedelta(minutes=16))['account_key'],KEY_B)

    def test_preflight_network_problem_cannot_create_infinite_retries(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
            self.store.claim(CYCLE,NOW)
            with self.assertRaisesRegex(RefreshError,'no_login_attempt_to_retry'):
                self.store.fail(CYCLE,KEY_A,'temporary_login_failure',NOW)
            self.assertEqual(self.store.job(CYCLE,KEY_A)['attempts'],0)

    def test_exactly_three_attempts_then_manual(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
            for n in range(3):
                now=NOW+timedelta(hours=n*3)
                self.store.claim(CYCLE,now)
                self.store.request_started(CYCLE,KEY_A,now)
                self.store.fail(CYCLE,KEY_A,'mail_timeout',now)
            self.assertIsNone(self.store.claim(CYCLE,NOW+timedelta(days=1)))
            self.assertEqual(self.store.job(CYCLE,KEY_A)['phase'],'manual')
            self.assertEqual(self.store.job(CYCLE,KEY_A)['attempts'],3)

    def test_attempt_counter_survives_cycle_reentry(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
            self.store.claim(CYCLE,NOW)
            self.store.request_started(CYCLE,KEY_A,NOW)
            self.store.fail(CYCLE,KEY_A,'mail_timeout',NOW)
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW+timedelta(days=1))
            self.assertEqual(self.store.job(CYCLE,KEY_A)['attempts'],1)

    def test_captcha_stops_without_three_retries(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
            self.store.claim(CYCLE,NOW)
            self.store.fail(CYCLE,KEY_A,'challenge',NOW)
            self.assertEqual(self.store.job(CYCLE,KEY_A)['phase'],'manual')
            self.assertEqual(self.store.job(CYCLE,KEY_A)['attempts'],0)

    def test_shared_outage_does_not_consume_login_attempts(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
            self.store.pause_shared(NOW+timedelta(hours=1))
            self.assertIsNone(self.store.claim(CYCLE,NOW))
            self.assertEqual(self.store.job(CYCLE,KEY_A)['attempts'],0)

    def test_publish_requires_verified_candidate(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
            self.store.claim(CYCLE,NOW)
            with self.assertRaisesRegex(RefreshError,'invalid_transition'):
                self.store.publish_started(CYCLE,KEY_A)

    def test_wrong_candidate_never_reaches_publish(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
            self.store.claim(CYCLE,NOW)
            self.store.request_started(CYCLE,KEY_A,NOW)
            with self.assertRaisesRegex(RefreshError,'identity_mismatch'):
                self.store.candidate_verified(CYCLE,KEY_A,'other@example.com')
            self.assertEqual(self.store.job(CYCLE,KEY_A)['phase'],'manual')

    def test_candidate_unknown_identity_has_controlled_error(self):
        with self.store.exclusive_run():
            with self.assertRaisesRegex(RefreshError,'missing_identity'):
                self.store.candidate_verified(CYCLE,KEY_C,'third@example.com')

    def test_candidate_cannot_change_completed_job(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
            self.store.claim(CYCLE,NOW)
            self.store.request_started(CYCLE,KEY_A,NOW)
            self.store.candidate_verified(CYCLE,KEY_A,TARGET)
            self.store.publish_started(CYCLE,KEY_A)
            self.store.published(CYCLE,KEY_A)
            self.store.cloud_verified(CYCLE,KEY_A,TARGET)
            with self.assertRaisesRegex(RefreshError,'invalid_transition'):
                self.store.candidate_verified(CYCLE,KEY_A,'other@example.com')
            self.assertEqual(self.store.job(CYCLE,KEY_A)['phase'],'done')

    def test_valid_full_state_sequence(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
            self.store.claim(CYCLE,NOW)
            self.store.request_started(CYCLE,KEY_A,NOW)
            self.store.candidate_verified(CYCLE,KEY_A,TARGET)
            self.store.publish_started(CYCLE,KEY_A)
            self.store.published(CYCLE,KEY_A)
            self.store.cloud_verified(CYCLE,KEY_A,TARGET)
        self.assertEqual(self.store.job(CYCLE,KEY_A)['phase'],'done')
        self.assertEqual(self.store.job(CYCLE,KEY_A)['account_key'],KEY_A)

    def test_ambiguous_publish_resumes_verification_not_relogin(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
            self.store.claim(CYCLE,NOW)
            self.store.request_started(CYCLE,KEY_A,NOW)
            self.store.candidate_verified(CYCLE,KEY_A,TARGET)
            self.store.publish_started(CYCLE,KEY_A)
            self.assertEqual(self.store.recovery_action(CYCLE,KEY_A,NOW),'reconcile_cloud_without_new_login')
            with self.assertRaisesRegex(RefreshError,'resume_verification_do_not_relogin'):
                self.store.fail(CYCLE,KEY_A,'temporary_login_failure',NOW)

    def test_request_interruption_waits_instead_of_resending(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
            self.store.claim(CYCLE,NOW)
            self.store.request_started(CYCLE,KEY_A,NOW)
            self.assertEqual(self.store.recovery_action(CYCLE,KEY_A,NOW+timedelta(minutes=2)),'wait_for_request_expiry')
            self.assertEqual(self.store.recovery_action(CYCLE,KEY_A,NOW+timedelta(minutes=11)),'expire_attempt_then_retry')

    def test_live_job_prevents_second_login(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A,KEY_B],NOW)
            self.store.claim(CYCLE,NOW)
            with self.assertRaisesRegex(RefreshError,'unfinished_job_requires_resume'):
                self.store.claim(CYCLE,NOW)

    def test_unknown_failure_text_not_persisted(self):
        with self.store.exclusive_run():
            self.store.ensure_cycle(CYCLE,[KEY_A],NOW)
            self.store.claim(CYCLE,NOW)
            with self.assertRaisesRegex(RefreshError,'unknown_failure_reason'):
                self.store.fail(CYCLE,KEY_A,'cookie=secret',NOW)
        self.assertNotIn('cookie', str(self.store.snapshot(CYCLE)))

    def test_invalid_key_path_rejected(self):
        with self.assertRaisesRegex(RefreshError,'invalid_account_key'):
            self.store.identity('../../anywhere')

    def test_month_end(self):
        self.assertEqual(next_month(datetime(2026,1,31,tzinfo=UTC)),datetime(2026,2,28,tzinfo=UTC))

    def test_leap_month_end(self):
        self.assertEqual(next_month(datetime(2028,1,31,tzinfo=UTC)),datetime(2028,2,29,tzinfo=UTC))

    def test_month_not_thirty_days(self):
        self.assertEqual(next_month(datetime(2026,8,31,tzinfo=UTC)),datetime(2026,9,30,tzinfo=UTC))


if __name__ == '__main__':
    unittest.main()
