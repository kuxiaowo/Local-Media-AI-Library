import { useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { Bot, Database, FolderCog, Images, Menu, Search, Settings, TimerReset, X } from 'lucide-react';

const navItems = [
  { to: '/media', label: '浏览', icon: Images },
  { to: '/search', label: '搜索', icon: Search },
  { to: '/agent', label: 'Agent', icon: Bot },
  { to: '/scan', label: '扫描', icon: TimerReset },
  { to: '/library', label: '目录设置', icon: FolderCog },
  { to: '/settings', label: '设置', icon: Settings },
];

export function Layout() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <div className="min-h-screen bg-[#eef1f4] text-ink">
      <header className="sticky top-0 z-40 flex h-14 items-center justify-between border-b border-line bg-white px-4 lg:hidden">
        <div className="flex items-center gap-2">
          <Database className="h-5 w-5 text-accent" />
          <div className="text-sm font-semibold">Local Media AI</div>
        </div>
        <button
          className="btn btn-secondary h-9 px-2"
          type="button"
          aria-label="打开导航"
          onClick={() => setMobileNavOpen(true)}
        >
          <Menu className="h-4 w-4" />
        </button>
      </header>

      <aside className="fixed left-0 top-0 z-30 hidden h-screen w-64 border-r border-line bg-white lg:block">
        <SidebarContent />
      </aside>

      {mobileNavOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            className="absolute inset-0 bg-black/35"
            type="button"
            aria-label="关闭导航"
            onClick={() => setMobileNavOpen(false)}
          />
          <aside className="relative h-full w-72 max-w-[86vw] border-r border-line bg-white shadow-xl">
            <div className="flex h-14 items-center justify-between border-b border-line px-4">
              <div className="flex items-center gap-2">
                <Database className="h-5 w-5 text-accent" />
                <div className="text-sm font-semibold">Local Media AI</div>
              </div>
              <button
                className="btn btn-secondary h-9 px-2"
                type="button"
                aria-label="关闭导航"
                onClick={() => setMobileNavOpen(false)}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <SidebarNav onNavigate={() => setMobileNavOpen(false)} />
          </aside>
        </div>
      )}

      <main className="min-w-0 lg:pl-64">
        <div className="mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:px-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

function SidebarContent() {
  return (
    <>
      <div className="flex h-16 items-center gap-3 border-b border-line px-5">
        <Database className="h-6 w-6 text-accent" />
        <div>
          <div className="text-sm font-semibold">Local Media AI</div>
          <div className="text-xs text-slate-500">Windows Web UI</div>
        </div>
      </div>
      <SidebarNav />
    </>
  );
}

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="space-y-1 p-3">
      {navItems.map((item) => {
        const Icon = item.icon;
        return (
          <NavLink
            key={item.to}
            to={item.to}
            onClick={onNavigate}
            className={({ isActive }) =>
              [
                'flex h-10 items-center gap-3 rounded-md px-3 text-sm font-medium transition',
                isActive
                  ? 'bg-accent text-white'
                  : 'text-slate-700 hover:bg-slate-100 hover:text-ink',
              ].join(' ')
            }
          >
            <Icon className="h-4 w-4" />
            {item.label}
          </NavLink>
        );
      })}
    </nav>
  );
}
