window.GAME_CONFIG = {
  // ===== Supabase (вставишь на Этапе 2) =====
  SUPABASE_URL: "",
  SUPABASE_ANON_KEY: "",

  // ===== Round / tournament =====
  roundId: 1,
  roundEndsAt: null, // пример: "2026-03-02T21:00:00Z"

  // ===== Game =====
  totalWords: 20,
  timeTotalSec: 120,
  maxHints: 3,

  // Уровни, которые будут попадать в игру:
  // ["A1"] или ["A1","A2"] или ["A1","A2","B1"]
  levels: ["A1", "A2", "B1"],

  // leaderboard
  topSize: 5
};
