import json
import tempfile
import unittest
from pathlib import Path
from helm_remote.store import Store
from helm_remote.collector import collect_file

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root/'db'); self.addCleanup(self.store.close)
    def test_codex_imports_assistant_only_and_deduplicates(self):
        p=self.root/'codex.jsonl'
        records=[{'type':'session_meta','payload':{'id':'abc','cwd':'/projects/bayzyl'}},
                 {'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':'The plugin checks passed.'}]}},
                 {'type':'response_item','payload':{'type':'function_call_output','output':'private data from a tool'}},
                 {'type':'response_item','payload':{'type':'message','role':'assistant','channel':'analysis','content':[{'type':'output_text','text':'private reasoning'}]}}]
        p.write_text('\n'.join(json.dumps(x) for x in records))
        collect_file(self.store,p,'codex'); collect_file(self.store,p,'codex')
        snap=self.store.snapshot()
        self.assertEqual(len(snap['updates']),1)
        self.assertEqual(snap['updates'][0]['text'],'The plugin checks passed.')
        self.assertEqual(snap['projects'][0]['name'],'bayzyl')
        self.assertNotIn('private data',json.dumps(snap))
        self.assertNotIn('private reasoning',json.dumps(snap))
    def test_claude_uses_only_text_and_does_not_make_a_fake_question(self):
        p=self.root/'claude.jsonl'
        p.write_text(json.dumps({'type':'assistant','sessionId':'def','cwd':'/projects/studio','message':{'content':[{'type':'thinking','thinking':'hidden reasoning'},{'type':'text','text':'Which layout do you prefer?'}]}}))
        collect_file(self.store,p,'claude')
        snap=self.store.snapshot()
        self.assertEqual(len(snap['updates']),1)
        self.assertEqual(snap['questions'],[])
        self.assertNotIn('hidden reasoning',json.dumps(snap))

    def test_new_turn_clears_previous_finished_status(self):
        p=self.root/'resumed.jsonl'
        p.write_text('\n'.join(json.dumps(x) for x in [
            {'type':'session_meta','payload':{'id':'resume','cwd':'/projects/resumed'}},
            {'type':'event_msg','payload':{'type':'task_complete'}},
            {'type':'event_msg','payload':{'type':'task_started'}},
            {'type':'response_item','payload':{'type':'message','role':'assistant','channel':'commentary','content':[{'type':'output_text','text':'Checking the new change.'}]}}
        ]))
        collect_file(self.store,p,'codex')
        self.assertEqual(self.store.snapshot()['updates'][0]['state'],'working')
