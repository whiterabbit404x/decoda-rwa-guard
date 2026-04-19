UPDATE response_actions
SET mode = 'live'
WHERE mode = 'live_enforcement';

UPDATE response_actions
SET status = CASE
    WHEN status IN ('planned', 'approved') THEN 'pending'
    WHEN status = 'rolled_back' THEN 'canceled'
    ELSE status
END
WHERE status IN ('planned', 'approved', 'rolled_back');
