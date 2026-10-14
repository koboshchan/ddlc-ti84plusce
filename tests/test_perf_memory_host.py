from pathlib import Path
import subprocess, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]
class MemoryLedger(unittest.TestCase):
    def test_alloc_free_unload_reload_overflow(self):
        harness=r"""
#define DDLC_PERF_OVERLAY 1
#define DDLC_PERF_MEMORY_IMPL
#include "perf_memory.c"
#include <assert.h>
int main(void) {
    perf_memory_t m=perf_memory_snapshot();
    assert(m.bytes==0 && m.count==0 && m.complete);
    void *a=perf_memory_malloc(19), *b=perf_memory_malloc(1001);
    assert(a && b);
    m=perf_memory_snapshot();assert(m.bytes==1020 && m.count==2 && m.peak==1020);
    perf_memory_free(a);perf_memory_free(NULL);
    m=perf_memory_snapshot();assert(m.bytes==1001 && m.count==1);
    perf_memory_free(b);
    m=perf_memory_snapshot();assert(m.bytes==0 && m.count==0 && m.peak==1020);
    a=perf_memory_malloc(2048);
    m=perf_memory_snapshot();assert(m.bytes==2048 && m.count==1 && m.peak==2048);
    perf_memory_free(a);
    void *items[65];
    for(unsigned i=0;i<65;i++){items[i]=perf_memory_malloc(10);assert(items[i]);}
    m=perf_memory_snapshot();assert(m.bytes==640 && m.count==64 && !m.complete);
    for(unsigned i=0;i<65;i++)perf_memory_free(items[i]);
    m=perf_memory_snapshot();assert(m.bytes==0 && m.count==0 && !m.complete);
}
"""
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/"test.c").write_text(harness)
            subprocess.run(["cc","-Wall","-Wextra","-Werror","-fsanitize=address,undefined",
                            "-I",str(ROOT/"src"),str(p/"test.c"),"-o",str(p/"test")],check=True)
            subprocess.run([str(p/"test")],check=True)
    def test_all_engine_owners_are_instrumented(self):
        for file in (ROOT/"src").glob("*.c"):
            if file.name=="perf_memory.c":continue
            text=file.read_text()
            import re
            if re.search(r"\b(malloc|free)\(",text):
                self.assertIn('#include "perf_memory.h"',text,file.name)
    def test_dev_menu_only_and_default_off(self):
        main=(ROOT/"src/main.c").read_text()
        self.assertNotIn("perf_overlay_key",main)
        self.assertNotIn("kb_KeyGraph",main)
        self.assertIn('"Performance overlay: %s"',main)
        module=(ROOT/"src/perf_overlay.c").read_text()
        self.assertIn("static bool enabled, owns_timer, measuring;",module)
if __name__=="__main__":unittest.main()
