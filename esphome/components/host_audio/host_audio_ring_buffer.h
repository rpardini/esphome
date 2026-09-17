#pragma once

#ifdef USE_HOST

#include <algorithm>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <memory>
#include <new>

namespace esphome::host_audio {

/// Lock-free byte ring buffer for exactly one writer thread and one reader thread.
///
/// The storage is allocated once. ``reset()`` may only be called while neither side is active.
class SpscRingBuffer {
 public:
  bool allocate(size_t capacity) {
    // One byte stays free to tell a full buffer from an empty one
    this->data_ = std::unique_ptr<uint8_t[]>(new (std::nothrow) uint8_t[capacity + 1]);
    this->size_ = this->data_ != nullptr ? capacity + 1 : 0;
    this->reset();
    return this->data_ != nullptr;
  }

  size_t capacity() const { return this->size_ > 0 ? this->size_ - 1 : 0; }

  size_t available() const {
    size_t head = this->head_.load(std::memory_order_acquire);
    size_t tail = this->tail_.load(std::memory_order_acquire);
    return head >= tail ? head - tail : this->size_ - tail + head;
  }

  size_t free() const { return this->capacity() - this->available(); }

  /// Writer side. Copies up to ``length`` bytes and returns how many were copied.
  size_t write(const uint8_t *data, size_t length) {
    size_t head = this->head_.load(std::memory_order_relaxed);
    size_t tail = this->tail_.load(std::memory_order_acquire);
    size_t free = head >= tail ? this->size_ - 1 - (head - tail) : tail - head - 1;
    length = std::min(length, free);
    size_t first = std::min(length, this->size_ - head);
    std::memcpy(&this->data_[head], data, first);
    std::memcpy(&this->data_[0], data + first, length - first);
    this->head_.store((head + length) % this->size_, std::memory_order_release);
    return length;
  }

  /// Reader side. Copies up to ``length`` bytes and returns how many were copied.
  size_t read(uint8_t *data, size_t length) {
    size_t tail = this->tail_.load(std::memory_order_relaxed);
    size_t head = this->head_.load(std::memory_order_acquire);
    size_t used = head >= tail ? head - tail : this->size_ - tail + head;
    length = std::min(length, used);
    size_t first = std::min(length, this->size_ - tail);
    std::memcpy(data, &this->data_[tail], first);
    std::memcpy(data + first, &this->data_[0], length - first);
    this->tail_.store((tail + length) % this->size_, std::memory_order_release);
    return length;
  }

  void reset() {
    this->head_.store(0, std::memory_order_release);
    this->tail_.store(0, std::memory_order_release);
  }

 protected:
  std::unique_ptr<uint8_t[]> data_;
  size_t size_{0};
  std::atomic<size_t> head_{0};
  std::atomic<size_t> tail_{0};
};

}  // namespace esphome::host_audio

#endif  // USE_HOST
