import tempfile
import threading
import unittest
from pathlib import Path
from helm_remote.store import Store, Conflict

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.sqlite'
        self.store = Store(self.path)
        self.session = self.store.register('Bayzyl', '/projects/bayzyl', 'codex', 'session-a')
    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()
    def ask(self, **kw):
        return self.store.ask(self.session, 'Ship this candidate?', ['Keep local', 'Ship'], **kw)
    def test_answer_is_scoped_to_exact_question_and_session(self):
        q = self.ask()
        other = self.store.register('Prismet', '/projects/prismet', 'claude', 'session-b')
        self.store.reply(q['id'], 'Keep local', 'reply-1')
        with self.assertRaises(Conflict):
            self.store.receive(other, q['id'])
        self.assertEqual(self.store.receive(self.session, q['id'])['answer'], 'Keep local')
        self.assertEqual(self.store.snapshot()['questions'][0]['state'], 'answered')
        self.store.acknowledge(self.session, q['id'])
        self.assertEqual(self.store.snapshot()['questions'][0]['state'], 'delivered')
    def test_duplicate_retry_is_idempotent_and_cannot_replace_answer(self):
        q = self.ask()
        self.store.reply(q['id'], 'Keep local', 'reply-1')
        self.store.reply(q['id'], 'Keep local', 'reply-1')
        with self.assertRaises(Conflict):
            self.store.reply(q['id'], 'Ship', 'reply-2')
        with self.assertRaises(Conflict):
            self.store.reply(q['id'], 'Ship', 'reply-1')
    def test_expired_question_refuses_answer(self):
        q = self.ask(ttl=0)
        with self.assertRaises(Conflict):
            self.store.reply(q['id'], 'Ship', 'reply-1')
        self.assertEqual(self.store.snapshot()['questions'][0]['state'], 'expired')
    def test_cancelled_question_cannot_be_answered(self):
        q = self.ask()
        self.store.cancel(self.session, q['id'])
        with self.assertRaises(Conflict):
            self.store.reply(q['id'], 'Ship', 'r')
    def test_persists_updates_and_pending_questions_across_restart(self):
        self.store.update(self.session, 'working', 'Running plugin tests')
        self.ask()
        self.store.close()
        self.store = Store(self.path)
        snap = self.store.snapshot()
        self.assertEqual(snap['projects'][0]['name'], 'Bayzyl')
        self.assertEqual(snap['updates'][0]['text'], 'Running plugin tests')
        self.assertEqual(snap['questions'][0]['state'], 'pending')
    def test_concurrent_replies_have_only_one_winner(self):
        q = self.ask()
        wins = []
        def reply(i):
            try:
                self.store.reply(q['id'], str(i), str(i))
                wins.append(i)
            except Conflict:
                pass
        threads = [threading.Thread(target=reply, args=(i,)) for i in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(wins), 1)
    def test_secret_like_text_is_redacted_before_storage(self):
        self.store.update(self.session, 'working', 'Bearer abcdefghijklmnop token=secret-value sk-proj-abcdefghijklmnop')
        text = self.store.snapshot()['updates'][0]['text']
        self.assertNotIn('abcdefghijklmnop', text)
        self.assertNotIn('secret-value', text)
    def test_quiet_checkpoint_refreshes_the_session_without_claiming_work(self):
        self.store.update(self.session, 'idle', 'Nothing running')
        self.store.checkpoint(self.session, quiet=True)
        self.assertEqual(self.store.snapshot()['projects'][0]['sessions'][0]['state'], 'idle')
        self.store.checkpoint(self.session)
        self.assertEqual(self.store.snapshot()['projects'][0]['sessions'][0]['state'], 'working')
    def test_castra_is_a_provider_and_others_still_are_not(self):
        self.store.register('Castra', '/castra', 'castra', 'castra-phone')
        with self.assertRaises(ValueError): self.store.register('Other', '/other', 'gemini', 'g1')
    def test_invalid_empty_answer_and_unknown_status_are_rejected(self):
        q = self.ask()
        with self.assertRaises(ValueError): self.store.reply(q['id'], '  ', 'r')
        with self.assertRaises(ValueError): self.store.update(self.session, 'perfect', 'hi')

if __name__ == '__main__': unittest.main()
