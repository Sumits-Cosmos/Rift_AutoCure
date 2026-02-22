import { Activity, Cpu, BookOpen, Sparkles } from 'lucide-react';

/**
 * Navigation Bar Component — Glassmorphic dark nav
 */
const Navbar = ({ setPage, currentPage }) => {
  const navLinks = [
    { key: 'healing', label: 'Healing Agent', icon: Activity, primary: true },
    { key: 'features', label: 'Features', icon: Sparkles },
    { key: 'docs', label: 'Docs', icon: BookOpen },
  ];

  return (
    <nav className="sticky top-0 z-50 flex items-center justify-between px-6 md:px-10 py-3 border-b border-white/[0.06] bg-[#0a0e1a]/80 backdrop-blur-xl">
      {/* Logo */}
      <div
        className="flex items-center gap-2.5 cursor-pointer group"
        onClick={() => setPage('home')}
      >
        <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-indigo-200 to-purple-500 flex items-center justify-center shadow-lg shadow-indigo-500/20 group-hover:shadow-indigo-500/40 transition-all duration-300">
          {/* <Cpu size={18} className="text-white" /> */}
          <img src="/logo.png" alt="AutoCure Logo" className="w-7 h-7" />
        </div>
        <span className="text-lg font-bold tracking-tight text-white">
          Auto<span className="text-indigo-400">cure</span>
        </span>
      </div>

      {/* Nav Links */}
      <div className="flex items-center gap-2">
        {navLinks.map((link) => {
          const Icon = link.icon;
          const isActive = currentPage === link.key;

          if (link.primary) {
            return (
              <button
                key={link.key}
                id="nav-healing-agent"
                onClick={() => setPage(link.key)}
                className={`
                  flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-semibold transition-all duration-200
                  ${isActive
                    ? 'bg-gradient-to-r from-indigo-500 to-purple-600 text-white shadow-lg shadow-indigo-500/25'
                    : 'bg-gradient-to-r from-indigo-500/80 to-purple-600/80 text-white/90 hover:from-indigo-500 hover:to-purple-600 hover:shadow-lg hover:shadow-indigo-500/25'
                  }
                `}
              >
                <Icon size={15} />
                {link.label}
              </button>
            );
          }

          return (
            <button
              key={link.key}
              onClick={() => setPage(link.key)}
              className={`
                flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200
                ${isActive
                  ? 'bg-white/10 text-white border border-white/10'
                  : 'text-slate-400 hover:text-white hover:bg-white/[0.06]'
                }
              `}
            >
              <Icon size={15} />
              {link.label}
            </button>
          );
        })}
      </div>
    </nav>
  );
};

export default Navbar;
