import { ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { cn } from '@/lib/utils'
import {
  Receipt,
  Package,
  Clock,
  Settings,
  User,
  Coffee,
} from 'lucide-react'

interface AppLayoutProps {
  children: ReactNode
}

const navItems = [
  { path: '/', label: 'Расходы', icon: Receipt },
  { path: '/supplies', label: 'Поставки', icon: Package },
  { path: '/shift-closing', label: 'Смена', icon: Clock },
]

const shiftSubItems = [
  { path: '/cashier/shift-closing', label: 'Кассир', icon: User },
  { path: '/cafe/shift-closing', label: 'Кафе', icon: Coffee },
]

export function AppLayout({ children }: AppLayoutProps) {
  const location = useLocation()

  return (
    <div className="min-h-screen bg-background">
      {/* Desktop Sidebar */}
      <aside className="fixed inset-y-0 left-0 z-50 hidden w-64 md:flex flex-col sidebar-glass">
        {/* Logo */}
        <div className="flex h-16 items-center gap-3 px-6 border-b border-border">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-foreground font-bold">
            P
          </div>
          <span className="text-lg font-semibold">PizzBurg</span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-4 space-y-1">
          {navItems.map((item) => {
            const Icon = item.icon
            const isShiftClosing = item.path === '/shift-closing'
            const isActive = location.pathname === item.path ||
              (item.path !== '/' && location.pathname.startsWith(item.path)) ||
              (item.path === '/' && location.pathname === '/expenses') ||
              (isShiftClosing && (location.pathname.includes('shift-closing')))

            return (
              <div key={item.path}>
                <NavLink
                  to={item.path}
                  className={cn(
                    "flex items-center gap-3 px-4 py-2.5 rounded-xl text-sm font-medium transition-all",
                    isActive
                      ? "bg-primary/10 text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  )}
                >
                  <Icon className="h-5 w-5" />
                  {item.label}
                </NavLink>
                {/* Sub-items for Shift Closing */}
                {isShiftClosing && isActive && (
                  <div className="ml-8 mt-1 space-y-1">
                    {shiftSubItems.map((sub) => {
                      const SubIcon = sub.icon
                      const subActive = location.pathname === sub.path
                      return (
                        <NavLink
                          key={sub.path}
                          to={sub.path}
                          className={cn(
                            "flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-all",
                            subActive
                              ? "bg-primary/10 text-primary"
                              : "text-muted-foreground hover:bg-muted hover:text-foreground"
                          )}
                        >
                          <SubIcon className="h-4 w-4" />
                          {sub.label}
                        </NavLink>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </nav>

        {/* Bottom section */}
        <div className="p-4 border-t border-border">
          <NavLink
            to="/settings"
            className={cn(
              "flex items-center gap-3 px-4 py-2.5 rounded-xl text-sm font-medium transition-all",
              location.pathname === '/settings'
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            <Settings className="h-5 w-5" />
            Настройки
          </NavLink>
        </div>
      </aside>

      {/* Main Content */}
      <main className="md:pl-64">
        <div className="min-h-screen pb-20 md:pb-0">
          {children}
        </div>
      </main>

      {/* Mobile Bottom Tab Bar */}
      <nav className="fixed inset-x-0 bottom-0 z-50 md:hidden glass border-t border-border pb-safe">
        <div className="flex items-center justify-around h-16">
          {navItems.map((item) => {
            const Icon = item.icon
            const isActive = location.pathname === item.path ||
              (item.path !== '/' && location.pathname.startsWith(item.path)) ||
              (item.path === '/' && location.pathname === '/expenses')

            return (
              <NavLink
                key={item.path}
                to={item.path}
                className={cn(
                  "flex flex-col items-center justify-center gap-1 w-16 py-1 rounded-xl transition-all",
                  isActive
                    ? "text-primary"
                    : "text-muted-foreground"
                )}
              >
                <Icon className={cn("h-6 w-6", isActive && "scale-110")} />
                <span className="text-[10px] font-medium">{item.label}</span>
              </NavLink>
            )
          })}
        </div>
      </nav>
    </div>
  )
}
