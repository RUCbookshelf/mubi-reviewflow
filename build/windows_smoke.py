"""Build gate using the installed runtime, including lazy native export/AI paths."""
from windows_start import check_dependencies
failures = check_dependencies()
assert not failures, failures
from sklearn.cluster import KMeans
assert len(KMeans(n_clusters=1, n_init=1, random_state=0).fit_predict([[0.0], [1.0]])) == 2
from coscreen.svg_render import svg_to_png
assert svg_to_png(b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="red"/></svg>', 20).startswith(b'\x89PNG')
from coscreen.research_trace_export import _activity_png
assert _activity_png([], {}, 'zh-CN').startswith(b'\x89PNG')
import launcher
assert launcher.build_app() is not None
print('Windows runtime, AI clustering, PNG exports and app startup: OK')
