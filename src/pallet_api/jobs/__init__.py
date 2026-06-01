"""Job lifecycle. arq owns enqueue/status/result storage in Redis (with TTL);
this package is reserved for any job helpers we add beyond what arq provides."""
