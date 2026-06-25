import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { LibrarySettingsPage } from './pages/LibrarySettingsPage';
import { MediaBrowserPage } from './pages/MediaBrowserPage';
import { MediaDetailPage } from './pages/MediaDetailPage';
import { ScanPage } from './pages/ScanPage';
import { SearchPage } from './pages/SearchPage';
import { SettingsPage } from './pages/SettingsPage';

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/media" replace />} />
        <Route path="library" element={<LibrarySettingsPage />} />
        <Route path="scan" element={<ScanPage />} />
        <Route path="media" element={<MediaBrowserPage />} />
        <Route path="media/:id" element={<MediaDetailPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
