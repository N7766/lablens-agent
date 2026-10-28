# Round 3 targeted real DPO generation regression

Two exposed real-document questions, one attempt each,4modelcalls. Both old primary errors are corrected: SFT baseline recognized, configfalse not accepted as historical per-step runtime proof. Whole-report strict score1/2: d01 adds token-length wording without the supplied whitespace-count implementation qualification, and selectedtable rows omit columnheaders. Fullper-case reasons are in `grading/per-case.json`.

Scope is generation on originalretrieved parentdocuments, not freshretrieval, multi-turnAgent or independent human evaluation. The ordinary generation path does not enable workspace scopedreviewcontract. Keep these2/2primary outcomes separate from six-turnAgent scores.
