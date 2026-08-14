package main

import (
	"sync/atomic"
	"testing"
	"time"
)

func TestSunnyDetectionBatchSize(t *testing.T) {
	const key = "SUNNY_TEST_DETECTION_BATCH_SIZE"
	t.Setenv(key, "")
	if got := sunnyDetectionBatchSize(key, 12, 100); got != 12 {
		t.Fatalf("default batch size = %d, want 12", got)
	}
	t.Setenv(key, "0")
	if got := sunnyDetectionBatchSize(key, 12, 100); got != 1 {
		t.Fatalf("minimum batch size = %d, want 1", got)
	}
	t.Setenv(key, "250")
	if got := sunnyDetectionBatchSize(key, 12, 100); got != 100 {
		t.Fatalf("maximum batch size = %d, want 100", got)
	}
	t.Setenv(key, "7")
	if got := sunnyDetectionBatchSize(key, 12, 100); got != 7 {
		t.Fatalf("configured batch size = %d, want 7", got)
	}
}

func TestStreamSunnyDetectionBatchLimitsConcurrencyAndStreamsResults(t *testing.T) {
	candidates := []int{0, 1, 2, 3, 4, 5, 6, 7}
	release := make(chan struct{})
	var active int32
	var maximum int32
	results := streamSunnyDetectionBatch(candidates, 3, func(candidate int) int {
		current := atomic.AddInt32(&active, 1)
		for {
			observed := atomic.LoadInt32(&maximum)
			if current <= observed || atomic.CompareAndSwapInt32(&maximum, observed, current) {
				break
			}
		}
		if candidate != 0 {
			<-release
		}
		atomic.AddInt32(&active, -1)
		return candidate
	})

	select {
	case first := <-results:
		if first != 0 {
			t.Fatalf("first streamed result = %d, want 0", first)
		}
	case <-time.After(time.Second):
		t.Fatal("first result was not streamed while the rest of the batch was running")
	}

	close(release)
	seen := map[int]bool{0: true}
	for result := range results {
		seen[result] = true
	}
	if len(seen) != len(candidates) {
		t.Fatalf("received %d results, want %d", len(seen), len(candidates))
	}
	if got := atomic.LoadInt32(&maximum); got > 3 {
		t.Fatalf("maximum concurrency = %d, want at most 3", got)
	}
}
