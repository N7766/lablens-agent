package io.lablens.workspace;

import java.util.concurrent.atomic.AtomicBoolean;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DataAccessException;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** A bounded poller: restart recovery uses MySQL QUEUED rows, not an in-memory queue. */
@Component
public class BulkImportDispatcher {
    private static final Logger log=LoggerFactory.getLogger(BulkImportDispatcher.class);
    private final BulkImportStore store;
    private final boolean enabled;
    private final AtomicBoolean polling=new AtomicBoolean();
    public BulkImportDispatcher(BulkImportStore store,@Value("${lablens.import-dispatch-enabled:${lablens.dispatch-enabled:true}}") boolean enabled) { this.store=store; this.enabled=enabled; }
    @Scheduled(fixedDelayString="${lablens.import-poll-ms:500}")
    public void poll() {
        if (!enabled || !polling.compareAndSet(false,true)) return;
        try { store.processNext(); }
        catch (DataAccessException e) {
            // SQL rollback preserves QUEUED. Retrying an atomic local transaction cannot
            // repeat a provider charge; import never sends data to a model provider.
            log.warn("Bulk import transaction rolled back; a later poll can resume ({})",e.getClass().getSimpleName());
        } finally { polling.set(false); }
    }
}
