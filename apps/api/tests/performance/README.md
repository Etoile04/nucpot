# Performance Testing Documentation

## Overview

Performance testing validates the MD verification system's ability to handle expected load with acceptable response times. We use **Locust** for load testing because it provides:

- Real-time performance metrics
- Web UI for test monitoring
- Distributed load testing capability
- Custom user behavior patterns

## Installation

Performance testing dependencies are in `pyproject.toml` under `[project.optional-dependencies.performance]`.

```bash
# Install performance testing dependencies
cd apps/api
uv sync --extra performance
```

## Running Performance Tests

### 1. Web UI Mode (Recommended for Development)

Start Locust web interface for interactive testing:

```bash
cd apps/api
uv run --with performance locust -f tests/performance/locustfile.py
```

Then open **http://localhost:8089** in your browser:

- Set number of users (start with 10)
- Set spawn rate (users/second)
- Set run time (or let it run indefinitely)
- Click "Start swarming"

### 2. Command Line Mode (Quick Tests)

Run from command line without web UI:

```bash
# Light load test: 10 users, spawn 2 per second, run for 5 minutes
uv run --with performance locust -f tests/performance/locustfile.py \
  --users 10 --spawn-rate 2 --run-time 5m --host http://localhost:8000

# Medium load test: 50 users
uv run --with performance locust -f tests/performance/locustfile.py \
  --users 50 --spawn-rate 5 --run-time 10m --host http://localhost:8000

# Heavy load test: 100 users
uv run --with performance locust -f tests/performance/locustfile.py \
  --users 100 --spawn-rate 10 --run-time 15m --host http://localhost:8000
```

### 3. Headless Mode (CI/CD)

For automated testing in CI/CD pipelines:

```bash
# Run without web UI, output results to terminal
uv run --with performance locust -f tests/performance/locustfile.py \
  --headless --users 20 --spawn-rate 2 --run-time 5m \
  --host http://localhost:8000 --csv performance_results

# HTML report will be generated
```

### 4. Stress Testing

Test system limits:

```bash
# Stress test: 200 users to find breaking point
uv run --with performance locust -f tests/performance/locustfile.py \
  --users 200 --spawn-rate 20 --run-time 10m \
  --host http://localhost:8000
```

## Performance Targets

### API Response Times

| Metric | Target | Acceptable |
|--------|--------|------------|
| p50 (median) | < 500ms | < 1000ms |
| p95 | < 2000ms (2s) | < 3000ms (3s) |
| p99 | < 5000ms (5s) | < 10000ms (10s) |

### Throughput

| Scenario | Target Users | Target RPS |
|----------|--------------|-----------|
| Light load | 10 | > 5 |
| Medium load | 50 | > 20 |
| Heavy load | 100 | > 40 |

### Task Queue

- **Throughput**: > 10 jobs/minute processing capacity
- **Queue depth**: < 100 jobs in queue under normal load
- **No job starvation**: Jobs should not wait > 5 minutes under normal load

### Error Rates

- **Normal load (< 50 users)**: < 1% errors
- **Heavy load (50-100 users)**: < 5% errors
- **Stress test (> 100 users)**: System should remain functional (no crashes)

## Test Scenarios

### 1. Baseline Performance Test

```bash
# Establish baseline performance
uv run --with performance locust -f tests/performance/locustfile.py \
  --users 10 --spawn-rate 1 --run-time 5m \
  --host http://localhost:8000 \
  --html baseline_report.html
```

### 2. Ramp-up Test

Gradually increase load to find performance threshold:

```bash
# Start with 10 users, ramp to 100 over 20 minutes
uv run --with performance locust -f tests/performance/locustfile.py \
  --users 100 --spawn-rate 0.8 --run-time 20m \
  --host http://localhost:8000
```

### 3. Spike Test

Test system recovery from sudden load spikes:

```bash
# Start with 10 users, spike to 100 for 2 minutes, then back to 10
# (Manual testing via Web UI recommended for spike tests)
```

### 4. Soak Test

Test system stability over extended periods:

```bash
# Run for 30 minutes with 20 users
uv run --with performance locust -f tests/performance/locustfile.py \
  --users 20 --spawn-rate 2 --run-time 30m \
  --host http://localhost:8000
```

## Interpreting Results

### Locust Web UI

When tests run, watch these metrics in the web UI:

1. **Requests/s**: Target throughput
2. **Failures/s**: Should be close to 0
3. **Median Response Time**: Should stay below targets
4. **Average Response Time**: Overall performance indicator
5. **Min/Max**: Check for outliers

### Command Line Output

Key metrics in terminal output:

```
Type                                                                    Name                                      # reqs      # fails |    Avg     Min     Max  | Median  | req/s
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
GET                                                                      /jobs/list                                1,234         0 |   450     200  1,200  |   420   |  12.3
GET                                                                      /jobs/detail                                456         0 |   680     300  2,500  |   650   |  4.5
POST                                                                     /jobs/submit                                  78         0 |  3,200   1,500  8,000  |  2,900  |  0.8
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
                                                                         Total                                    1,768         0 |   521     200  8,000  |   510   |  17.6
```

### Performance Report Files

Generate detailed reports:

```bash
# HTML report with charts
uv run --with performance locust -f tests/performance/locustfile.py \
  --headless --users 50 --spawn-rate 5 --run-time 5m \
  --html performance_report.html --host http://localhost:8000

# CSV report for analysis
uv run --with performance locust -f tests/performance/locustfile.py \
  --headless --users 50 --spawn-rate 5 --run-time 5m \
  --csv perf_results --host http://localhost:8000
```

## Common Issues and Solutions

### 1. "Connection refused" Errors

**Problem**: Can't connect to API server

**Solution**: Ensure API is running:
```bash
cd apps/api
uv run uvicorn src.main:app --reload
```

### 2. High Failure Rate

**Problem**: Many requests failing (> 5%)

**Common causes**:
- API server overloaded (check CPU/memory)
- Database connection pool exhausted
- Redis/Celery worker not responding

**Solutions**:
- Reduce load (fewer users)
- Check server logs: `apps/api/logs/app.log`
- Verify Celery workers: `celery -A src.worker.celery_app inspect active`

### 3. Slow Response Times

**Problem**: p95 > 2000ms consistently

**Common bottlenecks**:
- Database queries (check slow queries)
- HPC cluster latency
- N+1 query problems

**Solutions**:
- Enable query logging: Set `LOG_LEVEL=DEBUG`
- Check database connection pool
- Review query plans

### 4. "429 Too Many Requests"

**Problem**: Rate limiting triggered

**Solution**: Reduce spawn rate or add delays:
```python
# In locustfile.py, increase wait_time
wait_time = between(2, 5)  # Slower pace
```

## Performance Monitoring

### During Tests

Monitor system resources:

```bash
# Terminal 1: Run performance test
uv run --with performance locust -f tests/performance/locustfile.py

# Terminal 2: Monitor system resources
htop  # or top

# Terminal 3: Monitor database
psql postgresql://user:pass@localhost/nucpot
SELECT * FROM pg_stat_activity WHERE datname = 'nucpot';

# Terminal 4: Monitor Redis/Celery
redis-cli
> monitor
```

### Key Metrics to Watch

- **CPU**: Should stay < 80% under normal load
- **Memory**: Should stay < 80% of available RAM
- **Database connections**: Active connections should stay < pool size
- **Queue depth**: Celery queue should not grow unbounded
- **API latency**: Should stay < 2s (p95)

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Performance Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  performance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: "3.12"

      - name: Install uv
        run: pip install uv

      - name: Install dependencies
        run: |
          cd apps/api
          uv sync --extra performance

      - name: Start API server
        run: |
          cd apps/api
          uv run uvicorn src.main:app &
          sleep 10

      - name: Run performance tests
        run: |
          cd apps/api
          uv run --with performance locust -f tests/performance/locustfile.py \
            --headless --users 50 --spawn-rate 5 --run-time 5m \
            --host http://localhost:8000

      - name: Upload results
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: performance-results
          path: apps/api/performance_results_*
```

## Troubleshooting

### Enable Debug Logging

```bash
# Set environment variable
export LOG_LEVEL=DEBUG

# Run Locust with debug output
uv run --with performance locust -f tests/performance/locustfile.py \
  --loglevel DEBUG
```

### Check Locust Logs

```bash
# Run with file logging
uv run --with performance locust -f tests/performance/locustfile.py \
  --logfile locust.log --loglevel DEBUG

# View logs
tail -f locust.log
```

### Analyze Slow Requests

The locustfile logs slow requests (> 2s):

```bash
# Run Locust and watch for slow request warnings
uv run --with performance locust -f tests/performance/locustfile.py

# Check console output for "Slow request" warnings
```

## Performance Testing Best Practices

1. **Start small**: Begin with 10 users, increase gradually
2. **Monitor continuously**: Watch system resources during tests
3. **Test realistically**: Use realistic user behavior patterns
4. **Document baselines**: Record baseline performance for comparison
5. **Test in production-like environment**: Use staging server that mirrors production
6. **Run during off-peak hours**: Avoid affecting production users
7. **Analyze trends**: Look for performance degradation over time
8. **Test after changes**: Run performance tests after code/deployment changes

## Next Steps

1. **Establish baseline**: Run initial tests to establish performance baseline
2. **Set alerts**: Configure monitoring alerts for performance thresholds
3. **Optimize bottlenecks**: Address identified performance issues
4. **Re-test**: Verify optimizations improve performance
5. **Document**: Record test results and track performance over time

---

**Performance testing is critical for ensuring the MD verification system scales reliably as usage grows.**