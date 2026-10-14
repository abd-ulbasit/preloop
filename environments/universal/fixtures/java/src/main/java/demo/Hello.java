package demo;

/** Dependency-free fixture for javac and Maven. */
public final class Hello {
    private Hello() {}

    public static String greet(String name) {
        return "hello " + name;
    }

    public static void main(String[] args) {
        System.out.println(greet("world"));
    }
}
