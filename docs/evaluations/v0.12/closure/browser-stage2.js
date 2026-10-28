async (page) => {
 const posts=[];page.on('response',r=>{if(r.request().method()==='POST'&&/\/tasks$/.test(r.url()))posts.push(r);});
 const old=JSON.parse(await page.locator('#answer pre').textContent());
 const response=page.waitForResponse(r=>r.request().method()==='POST'&&/\/tasks$/.test(r.url()));
 await page.locator('#submit').click();const fresh=await (await response).json();
 await page.locator('#answer pre').filter({hasText:fresh.id}).waitFor({state:'attached'});
 await page.locator('#result-status').filter({hasText:'已完成'}).waitFor();
 const result=JSON.parse(await page.locator('#answer pre').textContent());
 if(result.task_id===old.task_id||result.status!=='SUCCEEDED')throw new Error('Explicit new attempt must succeed separately');
 if(await page.locator('.task').count()!==2)throw new Error('Old failure must be retained beside new success');
 await page.locator('#answer summary').click();
 await page.screenshot({path:'docs/evaluations/v0.12/closure/recovered-task.png'});
 await page.locator('.task').last().click();
 const oldAfter=JSON.parse(await page.locator('#answer pre').textContent());
 if(oldAfter.status!=='FAILED'||oldAfter.task_id!==old.task_id)throw new Error('Historical failure changed');
 await page.locator('#prepare-retry').click();await page.locator('#strategy').selectOption('dense');
 if(!(await page.locator('#submit').isDisabled()))throw new Error('Remote intent must request consent');
 await page.setViewportSize({width:390,height:844});
 if(!(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)))throw new Error('Mobile overflow');
 await page.setViewportSize({width:1440,height:1000});
 return {phase:'worker restored to real Python BM25',new_task:result,new_key:posts[0].request().headers()['idempotency-key'],old_task_preserved:oldAfter,checks:{explicit_new_attempt_succeeds:true,failed_history_unchanged:true,both_attempts_visible:true,remote_mode_still_requires_consent:true,mobile_no_horizontal_overflow:true},real_model_calls:0};
}
